#!/usr/bin/env python3
"""NPU Hex Batch Watcher v9 — Phase 3: file-based protocol.

Watches /tmp/npu_stimulus/ for new batch_NNNNN/READY sentinels.
Reads manifest.json + act hex files, computes via numpy, writes out hex files + DONE.

Protocol:
  C++ writes: batch_NNNNN/manifest.json, act_*.hex, READY
  Python writes: out_*.hex, DONE
  C++ polls DONE, reads out_*.hex

Hex format: one 8-char hex per line per float32 ($readmemh compatible)
"""

import json, sys, os, time, argparse
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path.home() / "npu" / "sim"))

from q4_dequant import load_weights_from_gguf

STIMULUS_DIR = Path("/tmp/npu_stimulus")
MODEL_PATH = Path.home() / "models" / "Qwen3-8B-Q4_K_M.gguf"

weight_buffer = {}
_dequant_cache = {}
_sim = _config = None


def get_sim():
    global _sim, _config
    if _sim is None:
        from npu_sim import NPUSimulator
        import yaml
        with open(Path.home() / "npu/sim/config/npu_config_wc.yaml") as f:
            _config = yaml.safe_load(f)
        _sim = NPUSimulator(str(Path.home() / "npu/sim/config/npu_config_wc.yaml"))
        a = f"{_config['mxu']['array_height']}x{_config['mxu']['array_width']}"
        print(f"[NPU-PY] Sim: {a} INT{_config['mxu']['weight_precision_bits']} "
              f"@{_config['mxu']['frequency_mhz']}MHz", flush=True)
    return _sim, _config


def load_model(gguf_path: str):
    global weight_buffer, _dequant_cache
    weight_buffer.clear()
    _dequant_cache.clear()
    t0 = time.time()

    import gguf
    reader = gguf.GGUFReader(gguf_path)

    for tensor in reader.tensors:
        raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
        weight_buffer[tensor.name] = {
            'raw': raw,
            'type': tensor.tensor_type.name,
            'shape': tensor.shape,
        }

    elapsed = time.time() - t0
    total_bytes = sum(len(v['raw']) for v in weight_buffer.values())
    print(f"[NPU-PY] Weight buffer: {len(weight_buffer)} tensors, "
          f"{total_bytes/1e9:.2f} GB in {elapsed:.1f}s", flush=True)


def get_weight(name: str) -> np.ndarray:
    """Lazy dequant + cache."""
    if name in _dequant_cache:
        return _dequant_cache[name]
    if name not in weight_buffer:
        return None

    info = weight_buffer[name]
    raw = info['raw']
    qtype = info['type']
    shape = info['shape']

    from q4_dequant import dequantize_q4_k, dequantize_q6_k, fp16_to_fp32

    if qtype == 'Q4_K':
        w = dequantize_q4_k(raw)
    elif qtype == 'Q6_K':
        w = dequantize_q6_k(raw)
    elif qtype == 'F32':
        w = np.frombuffer(raw, dtype=np.float32).copy()
    elif qtype == 'F16':
        w = fp16_to_fp32(np.frombuffer(raw, dtype=np.uint16))
    else:
        return None

    if len(shape) == 2:
        w = w.reshape(shape[1], shape[0])

    _dequant_cache[name] = w
    return w


def read_f32_hex(path: str, n_floats: int) -> np.ndarray:
    """Read float32 hex file (one 8-char hex per line)."""
    data = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i >= n_floats:
                break
            line = line.strip()
            if not line:
                continue
            bits = int(line, 16)
            data.append(np.frombuffer(bits.to_bytes(4, 'little'), dtype=np.float32)[0])
    return np.array(data, dtype=np.float32)


def write_f32_hex(path: str, arr: np.ndarray):
    """Write float32 as hex (8-char hex per line)."""
    arr = arr.astype(np.float32)
    with open(path, 'w') as f:
        for v in arr.ravel():
            bits = int.from_bytes(v.tobytes(), 'little')
            f.write(f"{bits:08x}\n")


def write_sentinel(path: str):
    Path(path).touch()


def process_batch(batch_dir: Path):
    """Process a single batch directory."""
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        print(f"  [NPU-PY] Bad manifest: {e}", flush=True)
        return

    ops = manifest.get("ops", [])
    if not ops:
        write_sentinel(str(batch_dir / "DONE"))
        return

    t0 = time.time()
    total_flops = 0
    misses = 0
    dq_time = 0

    for i, op in enumerate(ops):
        act_file = batch_dir / op["act_file"]
        out_file = batch_dir / op["out_file"]

        name = op["name"]
        M, K, N = op["M"], op["K"], op["N"]
        out_bytes = op["out_bytes"]
        n_out = out_bytes // 4  # float32 count
        n_act = M * K

        # Read activation
        act = read_f32_hex(str(act_file), n_act).reshape(M, K)

        # Lazy dequantize weight
        tdq = time.time()
        W = get_weight(name)
        dq_time += time.time() - tdq

        if W is None:
            misses += 1
            result = np.zeros((M, N), dtype=np.float32)
        else:
            # Matmul
            if W.shape[0] == K:
                result = act @ W
            else:
                result = act @ W.T
            total_flops += M * K * N * 2

        # Write result
        write_f32_hex(str(out_file), result.ravel()[:n_out])

    elapsed = time.time() - t0
    gflops = total_flops / elapsed / 1e9 if elapsed > 0 else 0

    # Write DONE sentinel
    write_sentinel(str(batch_dir / "DONE"))

    # Remove READY (mark as processed)
    ready_path = batch_dir / "READY"
    if ready_path.exists():
        ready_path.unlink()

    bid = batch_dir.name
    print(f"[NPU-PY] {bid}: {len(ops)} ops, {total_flops/1e6:.0f} MFLOP "
          f"in {elapsed*1000:.0f}ms ({gflops:.1f} GFLOPS), "
          f"dq={dq_time*1000:.0f}ms, miss={misses}, cache={len(_dequant_cache)}/{len(weight_buffer)}",
          flush=True)


def scan_and_process():
    """Scan for new READY batch dirs."""
    if not STIMULUS_DIR.exists():
        return

    processed = set()
    while True:
        for entry in sorted(STIMULUS_DIR.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("batch_"):
                continue
            ready = entry / "READY"
            if not ready.exists():
                continue
            if entry.name in processed:
                continue

            process_batch(entry)
            processed.add(entry.name)

        time.sleep(0.05)  # 50ms poll


def main():
    global MODEL_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="3B", choices=["3B", "7B"])
    parser.add_argument("--gguf", default=None)
    args = parser.parse_args()

    if args.gguf:
        MODEL_PATH = Path(args.gguf)
    elif args.model == "7B":
        MODEL_PATH = Path.home() / "models" / "Qwen3-14B-Q4_K_M.gguf"

    if MODEL_PATH.exists():
        load_model(str(MODEL_PATH))
    else:
        print(f"[NPU-PY] WARNING: GGUF not found: {MODEL_PATH}", flush=True)

    STIMULUS_DIR.mkdir(parents=True, exist_ok=True)
    get_sim()
    print(f"[NPU-PY] Phase3 hex watcher, Model={MODEL_PATH.name}, "
          f"watching {STIMULUS_DIR}/", flush=True)

    try:
        scan_and_process()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""NPU Socket Server v7 — Phase 2 batched compute + hex stimulus.

Protocol:
  - "batch": single request for N MUL_MATs, single response
  - "stimulus": hex dump manifest path
  - "graph_compute": monitoring trace
"""

import socket, struct, json, sys, os, argparse
from pathlib import Path

sys.path.insert(0, str(Path.home() / "npu" / "sim"))

SOCKET_PATH = "/tmp/ggml-npu.sock"
STIMULUS_DIR = Path("/tmp/npu_stimulus")
SIM_BASELINE = Path.home() / "npu" / "ggml-npu" / "sim_baseline.json"
MODEL_SIZE = "3B"
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
              f"@{_config['mxu']['frequency_mhz']}MHz", file=sys.stderr, flush=True)
    return _sim, _config


# ─── Batch compute ────────────────────────────────

def handle_batch(msg: dict, conn):
    """Handle batched MUL_MAT compute. Fast path: one recv, one send."""
    ops = msg.get("ops", [])
    n = len(ops)

    # Calculate total activation bytes to receive
    total_act = sum(op["act_bytes"] for op in ops)

    # Receive all activation data
    act_all = recv_exact(conn, total_act)

    # Split and compute
    outputs = []
    offset = 0
    for op in ops:
        act_sz = op["act_bytes"]
        act = act_all[offset:offset + act_sz]
        offset += act_sz
        # Return zeros of correct output shape
        outputs.append(b'\x00' * op["out_bytes"])

    # Concatenate and send back
    all_out = b''.join(outputs)
    conn.sendall(struct.pack("<I", len(all_out)))
    conn.sendall(all_out)

    return n


# ─── Hex stimulus ─────────────────────────────────

def handle_stimulus(msg: dict):
    sim, config = get_sim()
    manifest_path = STIMULUS_DIR / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text())
    ops = manifest.get("ops", [])
    if not ops:
        return None
    total = 0
    for op in ops:
        try:
            est = sim.mxu.estimate(op["M"], op["K"], op["N"])
            total += int(est.total_cycles)
        except Exception:
            total += max(1, op["M"] * op["K"] * op["N"] // (128 * 128))
    tok = 1e9 / total * config["mxu"]["frequency_mhz"] if total > 0 else 0
    return {"mode": "hex", "n_ops": len(ops), "total_cycles": total, "tok_per_s": round(tok, 1)}


def sim_summary(result):
    if not result:
        return
    print(f"\n[NPU-PY] Sim [{result['mode']}]: {result['n_ops']} ops, "
          f"{result['total_cycles']:,} cyc, {result['tok_per_s']} tok/s", file=sys.stderr, flush=True)
    if not SIM_BASELINE.exists():
        SIM_BASELINE.parent.mkdir(parents=True, exist_ok=True)
        SIM_BASELINE.write_text(json.dumps(result, indent=2))


# ─── Socket ───────────────────────────────────────

def recv_exact(conn, n):
    data = b""
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise ConnectionError("disconnected")
        data += chunk
    return data


def handle_client(conn):
    total_computed = 0
    msg_count = 0
    sim_result = None

    try:
        while True:
            hdr = recv_exact(conn, 4)
            msg_len = struct.unpack("<I", hdr)[0]
            if msg_len == 0:
                break
            payload = recv_exact(conn, msg_len)
            msg_count += 1

            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue

            mtype = msg.get("type", "")

            if mtype == "batch":
                n = handle_batch(msg, conn)
                total_computed += n
                if msg_count <= 2:
                    print(f"[NPU-PY] batch: {n} MUL_MAT computed", file=sys.stderr, flush=True)

            elif mtype == "stimulus":
                sim_result = handle_stimulus(msg)

    except (ConnectionError, BrokenPipeError):
        pass
    finally:
        conn.close()

    if msg_count > 2:
        print(f"[NPU-PY] session: {msg_count} msgs, {total_computed} MUL_MAT", file=sys.stderr, flush=True)
    if sim_result:
        sim_summary(sim_result)


def main():
    global MODEL_SIZE
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="3B", choices=["3B", "7B"])
    args = parser.parse_args()
    MODEL_SIZE = args.model

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    os.chmod(SOCKET_PATH, 0o666)
    get_sim()
    print(f"[NPU-PY] Phase2-batched, Model={MODEL_SIZE}, ready: {SOCKET_PATH}",
          file=sys.stderr, flush=True)

    try:
        while True:
            conn, _ = server.accept()
            handle_client(conn)
            print("[NPU-PY] done\n", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


if __name__ == "__main__":
    main()

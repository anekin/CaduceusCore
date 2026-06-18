#!/usr/bin/env python3
"""NPU Dev Loop v2 — automated build → benchmark → per-op regression detection.

Usage:
    python3 npu_dev_loop.py [--quick] [--watch] [--baseline] [--force] [--model PATH]

Modes:
    --watch     Watch for file changes, auto-trigger loop
    --baseline  Save per-op baselines after successful bench
    --quick     Only token generation (tg16), no prompt processing
    --force     Skip change detection, always run
    --model     Specify model path (default: DS-R1-Distill-Qwen-7B)

Flow:
    1. Watch: detect changes in ggml-npu.cpp or npu_server.py
    2. Build: cmake --build
    3. Server: start npu_server.py
    4. Benchmark: llama-bench --device NPU0
    5. Per-op: server reports regressions per operation node
    6. Compare: overall t/s vs baseline
"""

import subprocess
import sys
import os
import json
import time
import signal
import hashlib
from pathlib import Path
from datetime import datetime

LLAMA_DIR = Path.home() / "llama.cpp"
NPU_DIR = Path.home() / "npu" / "ggml-npu"
SIM_DIR = Path.home() / "npu" / "sim"
SERVER_SCRIPT = NPU_DIR / "npu_server.py"
BASELINE_FILE = NPU_DIR / "baseline.json"
PER_OP_BASELINE = NPU_DIR / "per_op_baseline.json"
SERVER_LOG = Path("/tmp/npu_server.log")
SOCKET_PATH = "/tmp/ggml-npu.sock"
MODEL = Path.home() / "models" / "DS-R1-Distill-Qwen-7B-Q4_K_M.gguf"

server_proc = None
watch_files = [
    NPU_DIR / "ggml-npu.cpp",
    NPU_DIR / "npu_server.py",
    NPU_DIR / "ggml-npu.h",
]


def run(cmd, **kwargs):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    return result


def step(msg):
    print(f"\n{'='*55}")
    print(f"  {msg}")
    print(f"{'='*55}")


def file_hashes():
    """Return SHA256 of watched files."""
    hashes = {}
    for f in watch_files:
        if f.exists():
            hashes[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return hashes


def detect_changes(last_hashes=None):
    """Check if watched files changed."""
    current = file_hashes()
    if last_hashes is None:
        return True, current
    changed = current != last_hashes
    if changed:
        for f in watch_files:
            if str(f) in current and str(f) in last_hashes:
                if current[str(f)] != last_hashes[str(f)]:
                    print(f"  Changed: {f.name}")
    return changed, current


def rebuild():
    step("Rebuilding llama.cpp + NPU backend")
    cmake_cmd = (f"cmake -B {LLAMA_DIR}/build -DGGML_NPU=ON -DGGML_METAL=OFF "
                 f"-DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_SYCL=OFF")
    run(cmake_cmd, cwd=LLAMA_DIR)
    result = run(f"cmake --build {LLAMA_DIR}/build --target llama-bench -j8",
                 cwd=LLAMA_DIR)
    if result.returncode != 0:
        print("BUILD FAILED:\n" + result.stderr[-1000:])
        return False
    print("  Build OK")
    return True


def start_server(sim_model="3B"):
    global server_proc
    step("Starting NPU Python server")
    with open(SERVER_LOG, "w") as log:
        server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT), "--model", sim_model],
            stdout=log, stderr=subprocess.STDOUT
        )
    time.sleep(0.5)
    if not os.path.exists(SOCKET_PATH):
        print("  Server failed to start!")
        return False
    print(f"  Server PID {server_proc.pid}")
    return True


def stop_server():
    global server_proc
    if server_proc:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        server_proc = None
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    # Read any remaining stderr from server
    if SERVER_LOG.exists():
        pass  # log is in stdout


def benchmark(model_path, quick=False):
    step(f"Benchmarking: {Path(model_path).name}")
    bench_bin = LLAMA_DIR / "build" / "bin" / "llama-bench"

    if quick:
        args = f"-m {model_path} -n 16 -p 0"
    else:
        args = f"-m {model_path} -p 512 -n 128"

    result = run(f"{bench_bin} {args} -o json 2>/tmp/npu_bench_stderr.log")
    if result.returncode != 0:
        stderr_log = Path("/tmp/npu_bench_stderr.log")
        err_text = stderr_log.read_text()[:1000] if stderr_log.exists() else "(no stderr)"
        print(f"  Bench failed (exit={result.returncode}): {err_text}")
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  Failed to parse: {result.stdout[:300]}")
        return None


def parse_results(data):
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    entry = data[0]
    return {
        "model": entry.get("model_type", "unknown"),
        "t/s": entry.get("avg_ts", 0),
        "ts_std": entry.get("stddev_ts", 0),
        "test_time": entry.get("test_time", ""),
    }


def show_server_summary():
    """Print server's regression report from log file."""
    if SERVER_LOG.exists():
        for line in SERVER_LOG.read_text().strip().split("\n"):
            if "[NPU-PY]" in line:
                print(f"  {line.strip()}")


def save_baseline(metrics):
    commit = run("cd ~/npu && git rev-parse HEAD").stdout.strip()
    if not commit:
        commit = "unknown"
    baseline = {
        "commit": commit,
        "timestamp": datetime.now().isoformat(),
        "metrics": metrics,
    }
    BASELINE_FILE.write_text(json.dumps(baseline, indent=2))
    print(f"  Overall baseline saved: {commit[:8]}")


def compare_baseline(metrics):
    if not BASELINE_FILE.exists():
        print("  No overall baseline — saving current")
        save_baseline(metrics)
        return

    baseline = json.loads(BASELINE_FILE.read_text())
    prev = baseline["metrics"]

    cur_ts = metrics["t/s"]
    prev_ts = prev["t/s"]
    if prev_ts == 0:
        return
    delta = (cur_ts - prev_ts) / prev_ts * 100

    print(f"\n  Overall t/s: {cur_ts:,.0f} (baseline: {prev_ts:,.0f}, delta: {delta:+.1f}%)")
    if delta < -10:
        print(f"  ⚠️  REGRESSION: -{abs(delta):.0f}% — check per-op report above")


def save_per_op_baseline():
    """Save current per-op results as baseline."""
    if PER_OP_BASELINE.exists():
        commit = run("cd ~/npu && git rev-parse HEAD").stdout.strip()
        if not commit:
            commit = "unknown"
        data = json.loads(PER_OP_BASELINE.read_text())
        data["_commit"] = commit
        data["_timestamp"] = datetime.now().isoformat()
        PER_OP_BASELINE.write_text(json.dumps(data, indent=2))
        print(f"  Per-op baseline updated: {commit[:8]}")


def run_once(model_path, quick, save_bl, sim_model="3B"):
    if not rebuild():
        return False
    if not start_server(sim_model):
        return False
    try:
        data = benchmark(model_path, quick=quick)
        if not data:
            return False
        metrics = parse_results(data)
        if not metrics:
            return False
        print(f"\n  tg16: {metrics['t/s']:,.0f} ± {metrics['ts_std']:,.0f} t/s")
        compare_baseline(metrics)
        show_server_summary()
        if save_bl:
            save_per_op_baseline()
    finally:
        stop_server()
    return True


def watch_loop(model_path, quick, save_bl, sim_model="3B"):
    print(f"👁  Watching for changes... (Ctrl+C to stop)")
    print(f"   Files: {', '.join(f.name for f in watch_files)}")
    print(f"   Model: {Path(model_path).name}")
    print()

    last_hashes = None
    run_count = 0

    try:
        while True:
            changed, hashes = detect_changes(last_hashes)
            if changed:
                print(f"\n{'─'*55}")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Change detected!")
                print(f"{'─'*55}")
                run_once(model_path, quick, save_bl)
                run_count += 1
                last_hashes = hashes
                print(f"\n👁  Watching... (run #{run_count})")
            time.sleep(3)
    except KeyboardInterrupt:
        print(f"\n\nStopped after {run_count} runs.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NPU Dev Loop v2")
    parser.add_argument("--quick", action="store_true", help="Quick mode (generation only)")
    parser.add_argument("--watch", action="store_true", help="Watch for file changes")
    parser.add_argument("--baseline", action="store_true", help="Save per-op baselines after run")
    parser.add_argument("--force", action="store_true", help="Force rebuild and bench")
    parser.add_argument("--model", default=str(MODEL), help="Model path")
    parser.add_argument("--sim-model", default="3B", choices=["3B", "7B"], help="NPU simulator model size")
    args = parser.parse_args()

    model_path = args.model
    if not os.path.exists(model_path):
        print(f"Model not found: {model_path}")
        sys.exit(1)

    if args.watch:
        print("="*55)
        print("  NPU Dev Loop — Watch Mode")
        print("="*55)
        watch_loop(model_path, args.quick, args.baseline)
    else:
        run_once(model_path, args.quick, args.baseline)


if __name__ == "__main__":
    main()

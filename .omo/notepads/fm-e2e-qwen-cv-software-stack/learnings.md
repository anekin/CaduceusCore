# fm-e2e-qwen-cv-software-stack — Learnings

## 2026-07-31 15:48 A1 — 36-layer golden reference generator

### Completed

- **Created `scripts/gen_qwen_full_golden.py`**: Generates N-layer golden reference for Qwen forward pass using the Func Model's float32 forward pass.
  - CLI: `--model`, `--layers` (reads from GGUF if not specified), `--output`, `--prompt`, `--use-llamacpp-dump` (optional llama.cpp cross-reference)
  - Output 1: Combined `.npz` (`qwen-{N}l-golden.npz`) with keys `l_out_0..l_out_{N-1}`, `logits`, `tokens`, `metadata`
  - Output 2: Per-layer `.npz` (`expected_l{N}.npz`) with keys `output`, `layer`, `metadata` — compatible with `scripts/run_36layer_checkpoint.py`

### Verified

- Smoked with 1-layer run: combined `.npz` produces correct keys, logits shape (151936,), token predicted (42787 for "Hello" prompt)
- Per-layer format verified: `run_36layer_checkpoint.py --layers 0` loads `expected_l0.npz` and reports `[PASS] L0: cos_sim=1.000000`
- Validation checks: all expected keys present, shapes consistent, no NaN/Inf detected
- `.gitignore` updated to exclude `.omo/evidence/*.npz`

### Key design decisions

- **Default: Func Model only** — uses `qwen25_forward.run_forward_pass()` to generate golden data. This is the primary path, producing data that `run_36layer_checkpoint.py` compares against (tautological for now, but unblocks A2/A3 which add NPU path comparison).
- **Optional llama.cpp cross-reference** — `--use-llamacpp-dump` flag runs the `dump_hidden_states` binary for external validation.
- **Logits computation**: Loads `output_norm.weight` (F32) and `output.weight` (Q6_K, dequantized) from GGUF, applies rms_norm + matmul.
- **Layer count**: Default reads `qwen2.block_count` from GGUF metadata (36 for 3B); `--layers N` overrides. No hardcoded 36.

### Model info (Qwen2.5-3B-Instruct-Q4_K_M)

- Layers: 36, Hidden: 2048, Heads: 16, KV heads: 2, Vocab: 151936

### For acceptance test

```bash
PYTHONPATH=sim:ggml-npu python3 scripts/gen_qwen_full_golden.py \
  --model ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
  --layers 36 \
  --output .omo/evidence/
# Then copy to checkpoint dir and verify:
cp .omo/evidence/expected_l*.npz rtl/test_vectors/soc_e2e/qwen25-3b-36layer/
PYTHONPATH=sim python3 scripts/run_36layer_checkpoint.py \
  --model ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
  --layers 0 10 20 35
```

### Known limitations

- `run_36layer_checkpoint.py` hardcodes `GOLDEN_DIR` — files must be copied there manually.
- GGUF weight loading takes ~100s (Q4_K/Q6_K dequant); forward pass alone <2s.

## 2026-07-31 15:55 A5 — managed device_server lifecycle fixture

### Completed

- **Created `sim/signoff/device_server_fixture.py`**: `managed_device_server(device_url="fm://python", timeout=5.0)` starts/stops `sim.device_server` for `fm://python`, `fm://`, `fm://spike` and yields the resolved `fm://unix?path=<pid-unique sock>` URI. Other URIs (`mock://`, resolved `fm://unix?path=...`) pass through untouched.
- **Wired into `scripts/run_qwen3b_software_signoff.py`** (positive path owns lifecycle). Runner keeps its internal wrap for direct API callers — it is a passthrough for already-resolved URIs.
- **New tests** `sim/tests/test_device_server_fixture.py` (7 tests): start/stop, second instantiation (no address-in-use), mock passthrough, resolved-URI passthrough, timeout→RuntimeError with log tail, spike detection, and the plan acceptance (script runs `full_shape_blk0` on `fm://python` with no pre-started server).

### Verified

- Acceptance: `PYTHONPATH=sim:gen python3 scripts/run_qwen3b_software_signoff.py positive --device fm://python` → **verdict pass, exit 0**; `full_shape_blk0` passed with `npu_ops_executed=543`. No leftover sockets/processes.
- `positive --device mock:// --gate full_shape_blk0` still passes (fixture does not manage mock).
- `PYTHONPATH=sim python -m pytest sim/tests/test_device_server_fixture.py -q` → 7 passed.
- No new pyflakes issues; pre-existing flags remain (see issues.md).

### Key design decisions

- **Explicit resolved URI** (`fm://unix?path=...`) instead of relying on `fm://python`→default-socket mapping: transport_fm.cpp maps `fm://python`/`fm://`/`fm://spike` all to `/tmp/caduceus_fm.sock`, but yielding the explicit form guarantees gates connect to the socket this fixture owns and never clash with a manually started server.
- **Log capture to temp file** (not PIPE): avoids pipe-buffer deadlock while polling readiness; failure diagnostics show last 10 log lines in the RuntimeError.
- **Socket readiness = real connect()** poll, not socket-file existence; startup measured ~0.25s so 5s default timeout is generous.
- **Spike gate selection** in the runner now uses `is_spike_device()`, which matches both the raw `fm://spike` and the resolved `...caduceus_fm_spike_<pid>.sock` forms (the script resolves before the runner sees the URI).

### Observations

- Evidence JSON `device_uri` now records the resolved `fm://unix?path=...` form.
- `pytest-timeout` is not installed; `@pytest.mark.timeout` is a no-op warning — use `subprocess.run(timeout=...)` for real guards.

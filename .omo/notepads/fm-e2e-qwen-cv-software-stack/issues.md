# fm-e2e-qwen-cv-software-stack — Issues

## Open

- **NEG-01 (pre-existing): `run_negative_signoff` corrupted_weight_detection always fails.**
  `sim/signoff/qwen3b_signoff_runner.py` (~line 168) references `REPO_ROOT` without importing it → `NameError`, swallowed by a bare `except` → `detected=False` → negative verdict always `fail`. Repro: `negative --device fm://python`. Fix: import `REPO_ROOT` from `qwen3b_signoff_config` (and narrow the bare except).
- **NEG-02 (pre-existing): `test_negative_signoff_detects_*` tests call `run_negative_signoff(cfg, evidence)` with 2 args** but the signature requires `device_uri` → TypeError on HEAD (`sim/tests/test_qwen3b_software_signoff.py:105,120`).
- **ENV-01 (pre-existing): `pytest-timeout` not installed** — `@pytest.mark.timeout` markers are no-ops (unknown-marker warnings).

## Resolved

- **A5 (this task):** fm://python gates required a manually pre-started `python -m sim.device_server`. Now auto-managed by the `managed_device_server` fixture in `sim/signoff/device_server_fixture.py`.

## Notes

- `run_36layer_checkpoint.py` hardcodes `GOLDEN_DIR`. Consider adding `--checkpoint-dir` in a future task for easier CI integration.
- The 36-layer forward takes ~100s dominated by GGUF load. For CI, consider caching dequantized weights or using a pre-generated golden.

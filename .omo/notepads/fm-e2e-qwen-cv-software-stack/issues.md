# fm-e2e-qwen-cv-software-stack — Issues

## Open

- (none)

## Resolved

- **A5 (this task):** fm://python gates required a manually pre-started `python -m sim.device_server`. Now auto-managed by the `managed_device_server` fixture in `sim/signoff/device_server_fixture.py`.
- **B1 (this task):** `software/compiler/command_ir_codec.py` had a bug where `encode_blob()` wrote multi-entry command rings at wrong offsets (used mutable `off` instead of stable `cmd_ring_off`). Fixed: `entry_off = cmd_ring_off + i * CAD_CMD_ENTRY_BYTES`. This made the production `CommandBlob.encode()`/`decode()` non-roundtrippable for any blob with >1 command entry. Now works correctly for MMUL+barrier, multi-MMUL, MMUL+SFU, and full MobileNetV3 graphs.
- **NEG-01:** `sim/signoff/qwen3b_signoff_runner.py` now imports `REPO_ROOT` from `qwen3b_signoff_config`. The `corrupted_weight_detection` negative check is skipped for non-FM devices and narrowed to catch only `OSError` when the runtime library is missing.
- **NEG-02:** `sim/tests/test_qwen3b_software_signoff.py` now passes `device_uri="mock://"` to both `run_negative_signoff` calls.
- **ENV-01:** `pytest-timeout>=2.3.0` added to `requirements.txt`; install with `pip install -r requirements.txt` to make `@pytest.mark.timeout` functional.

## Notes

- `run_36layer_checkpoint.py` hardcodes `GOLDEN_DIR`. Consider adding `--checkpoint-dir` in a future task for easier CI integration.
- The 36-layer forward takes ~100s dominated by GGUF load. For CI, consider caching dequantized weights or using a pre-generated golden.

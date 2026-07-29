# draft: close-e2e-command-gap

**intent**: CLEAR
**review_required**: false
**started**: 2026-07-28
**baseline_commit**: 9b4ae44
**source**: func-model-soc-golden-and-software-stack-review-2026-07-28.md

## Grounding evidence

### SW-01: Host Runtime only has NOP
- `software/include/caduceus/runtime.h:220`: `cadCommandListAppendNop()` — only command API. No `cadCommandListAppendExecuteBlob()` or equivalent.
- `software/src/runtime_core.c`: submit path passes `cmd_list->entries` to transport but entries are NOP-only.

### SW-02: FM transport discards payload
- `software/src/transport_fm.cpp:539-552`: `fm_submit()` accepts `cmd_data` but `(void)cmd_data;` at line 542. SubmitRequest has `cmd_blob` field but it's never populated.
- Server side IS ready: `sim/device_server.py:586` reads `req.cmdBlob` into `_PendingCommand`.

### SW-03: llama.cpp backend falls back to CPU
- (awaiting explore agent output)

### SW-05: Firmware signoff bypasses Host Runtime
- `scripts/run_runtime_spike_signoff.py`: directly constructs `FuncModel(use_spike=True)`, writes ring/doorbell in Python, bypassing Host Runtime entirely.

### SW-08: fpga:// maps to mock
- `software/src/runtime_core.c`: `fpga://` falls through to mock transport.

### SW-09: CI untrusted
- Qwen gate uses `--device mock://`
- `continue-on-error` on Spike checks
- aggregator `--no-stale-check`
- software/build/libcaduceus_runtime.so is a stale symlink

### SoC Golden Gaps
- GR-01: No frozen observable contract
- GR-02: No front-door hard gate
- GR-03: Python vs real firmware not locked
- GR-04: Differential anti-vacuity weak
- GR-07: Evidence not clean-checkout reproducible

## Decisions (adopted defaults)
- Use existing command_ir blob format (`cad_command_blob_t`) as wire payload
- Wrap in FlatBuffers SubmitRequest.cmdBlob (already in schema)
- Single MMUL as first non-NOP gate
- TDD throughout (existing test pattern)
- Fix CI: no continue-on-error, no mock for critical gates
- fpga:// returns UNSUPPORTED until real transport exists

## Components (pending confirmation)
1. Evidence / CI / Baseline Trust
2. Command API + Transport Payload Chain
3. Device Server + Firmware Execution via Host Runtime
4. Single MMUL Non-NOP End-to-End Gate
5. llama.cpp Real Offload (supported partition → FM)
6. SoC Golden Contract + Differential Anti-Vacuity

**scope_decision**: ONE mega-plan covering all 4 phases, 30-40 todos, sequential waves with max internal parallelism.

**status**: plan-generated, high-accuracy-review-pending
**plan_file**: .omo/plans/close-e2e-command-gap.md
**generated**: 2026-07-28

## High-accuracy review (dual — PASSED)
- **Round 1**: Momus REJECT (3 issues), Oracle REJECT (3 issues) — all fixed
- **Round 2**: Momus REJECT (3 issues), Oracle REJECT (5 issues) — all fixed
- **Round 3**: Momus ✅ OKAY, Oracle ✅ OKAY — both unconditional approval
- **Review complete**: 2026-07-28

## Enriched evidence (from 5 explore agents)

### Command path gap detail
- `runtime.h:220`: only `cadCommandListAppendNop()` — no blob append API
- `runtime_core.c:319-325`: `cadQueueSubmit` passes `cmd_list` as `void* cmd_data` to transport → transport sees raw pointer, no serialized payload
- `runtime_core.h:49-55`: `cad_command_list_impl_t` stores only `entry_count` and `max_entries` — **no command payload buffer exists**
- `transport_fm.cpp:539-542`: `fm_submit()` receives `cmd_data`, immediately `(void)cmd_data;`, constructs `SubmitRequest` with `cmd_count` only — never populates `cmd_blob`
- `transport_mock.c:259-262`: `mock_submit()` also discards `cmd_data`
- `transport_rtl.cpp:646-649`: `rtl_submit()` also discards `cmd_data`
- `transport_fpga.cpp:409-411`: `fpga_submit()` also discards `cmd_data`
- `device_protocol.fbs:78-82`: `SubmitRequest` defines `cmd_blob:[ubyte]` — schema exists, field unused
- `device_server.py:576-588`: `_do_submit()` reads `req.cmdBlob` into `_PendingCommand` — server side ready
- `device_server.py:304-347`: `_execute_on_model()` splits cmd_blob → ring entries (24B each) + descriptors (60B each), writes to DRAM via PCIe TLP, sets doorbell, calls `run_loop()` — execution path fully implemented

### llama.cpp backend detail
- `ggml-npu.cpp:402-454`: `npu_graph_compute()` — the main execution entry point
- `ggml-npu.cpp:416`: builds command IR blob via `npu_build_command_validation_blob()`
- `ggml-npu.cpp:419-439`: NPU side-effect path — encodes blob, writes to device buffer, submits **NOP only** (line 433)
- `ggml-npu.cpp:443-451`: CPU fallback — ALL real computation happens here via `ggml_backend_graph_compute(cpu, cgraph)`
- `ggml-npu.cpp:186-191`: function-level doc explicitly states "Actual computation is always delegated to the CPU backend"
- Backend `supports_op` (lines 595-796) comprehensively covers MMUL/SFU/Vector ops — the capability mapping is correct, just never used

### CI and aggregator detail
- CI line 21: `CADUCEUS_DEVICE: mock://` — global, inherited by all jobs
- CI line 94,97: L3 Spike steps have `continue-on-error: true` — masks failures
- CI line 119: Entire L5 job has `continue-on-error: true` — perpetual opt-out
- CI line 181: `--no-stale-check` — staleness disabled in CI
- aggregator lines 235, 206, 293-295, 327, 344-345: five "assume pass" fallback paths
- aggregator line 295: explicit philosophy comment — "existence is evidence of passing"
- `software/build/libcaduceus_runtime.so`: valid symlink (→ ../../build/software/...), 146992-byte ELF, x86-64, unstripped

### Device server / firmware path detail
- `func_model.py:78-99`: `use_spike=True` → `SpikeFirmware` (real RISC-V), else → `NPUFirmware` (Python mock)
- `npu_firmware.c:571-603`: `firmware_main()` — polls HOST_TAIL, WFI on idle, dispatches commands
- `npu_firmware.c:441-556`: `dispatch_cmd()` — MMUL (0x00) tile loop, SFU (0x01), ROPE (0x05), Vector (0x0F-0x14), PCIe_DMA (0x07), DMA_COPY (0x09+)
- `npu_firmware.c:423-431`: `read_cmd_entry()` — reads 32B ring entry from DRAM at `RING_BUF_ADDR + head*32`
- Ring layout: opcode(4B) + desc_addr(4B) + flags(4B) + pad(20B) = 32B in DRAM
- `device_server.py:325`: latent ABI mismatch — `struct.unpack_from("<IQI",...)` reads desc_addr as 8B, firmware reads as 4B (works currently because addresses < 4GB)

### Firmware descriptor ABI pinning
- `npu_firmware.c:125-147`: `_Static_assert` blocks verify every descriptor field offset against `gen/npu_abi_firmware.h`
- All descriptors use 15-word layout: `mmul_desc_t`, `sfu_desc_t`, `vector_desc_t`, `dma_copy_desc_t`
- Firmware `read_*_desc()` functions (lines 376-420) read 15×uint32 from DRAM at `desc_addr`

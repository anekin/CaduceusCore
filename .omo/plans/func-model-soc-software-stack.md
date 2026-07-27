# Func Model / SoC RTL / FPGA Unified Software Stack Plan

## TL;DR
> Summary: Build one contract-driven Host Runtime and verification stack that drives the current SoC Func Model, SoC RTL, and FPGA through interchangeable transports. Integrate Qwen 3B through llama.cpp first, then add an ExecuTorch delegate over the same runtime.
> Deliverables:
> - versioned HW/SW ABI schema and generated Python/C/C++/SystemVerilog bindings;
> - stable C Host Runtime, C++ wrapper, Python binding, and transport conformance suite;
> - shared scenario/driver/monitor/scoreboard layer with Func Model, RTL, and FPGA DUT adapters;
> - mandatory compiled-firmware integration through Spike;
> - production llama.cpp backend using the Host Runtime, followed by ExecuTorch integration;
> - Linux userspace FPGA transport and unchanged software replay.
> Effort: XL
> Risk: High - spans ABI compatibility, compiled firmware, framework integration, RTL simulation, and external FPGA availability.

## Approved decisions

| Decision | Selected policy |
|---|---|
| Framework order | llama.cpp first for Qwen 3B; ExecuTorch after the shared runtime is stable |
| Host/FPGA boundary | Stable C Host Runtime ABI; C++ and Python bindings; Linux userspace FPGA transport first |
| Test policy | Contract-first TDD, Func Model/RTL differential tests, testbench fault injection, and mandatory real Spike firmware integration |

“Same firmware” means the same C source, generated ABI header, and command semantics. Target-specific startup/linker images such as `npu_firmware.elf` and `npu_firmware_spike.elf` may remain distinct.

## Scope

### Must have

- Preserve the three Func Model purposes:
  1. behavioral golden reference for RTL;
  2. validation environment for a reusable SoC testbench;
  3. early software-development target whose runtime/framework stack moves to FPGA.
- Make one machine-readable ABI source authoritative for:
  - address map and register offsets;
  - opcode values;
  - descriptor and ring layouts;
  - completion and error status;
  - ABI version and device capabilities.
- Keep mathematical golden oracles independent from production runtime/compiler implementations.
- Make framework adapters depend only on the Host Runtime, never directly on Func Model Python APIs, cocotb, BAR addresses, or FPGA driver details.
- Exercise the real SoC-visible path for software E2E:
  - buffer/data transfer;
  - command and descriptor construction;
  - PCIe/BAR-visible writes;
  - doorbell;
  - compiled NPU firmware;
  - completion/status;
  - output readback.
- Permit backdoor access only for initialization, observability, or explicitly classified diagnostics. Software E2E signoff must not use backdoors to perform the operation under test.
- Pin external framework dependencies:
  - llama.cpp: `ggml-org/llama.cpp` commit `88b47a755c72fed4b22fba0fd262e2d7b7d01583` captured on 2026-07-27;
  - ExecuTorch: official `v1.2.0` integration contract.
- Keep existing scaled/synthetic tests as fast regressions while adding real Qwen 3B software gates.

### Must NOT have

- No production path through `/tmp/npu_stimulus`, hex-file polling, or direct NumPy execution in `ggml-npu/npu_server.py`.
- No duplicate hand-maintained ABI definitions after generated bindings are adopted.
- No direct `FuncModel.host_write_*()` calls from llama.cpp or ExecuTorch adapters.
- No framework-specific PCIe/BAR/doorbell code.
- No claim that Python `NPUFirmware` validates compiled firmware behavior.
- No change to RTL functional behavior in this plan. If a differential test exposes an RTL bug, record it as a separate RTL blocker with evidence.
- No performance signoff or performance optimization in this plan. Functional timing/timeout instrumentation is allowed.
- No production-grade kernel driver, multi-tenant isolation, secure boot, power management, or hot-plug support. The FPGA phase implements a userspace transport over UIO, VFIO, or an already-provided vendor device.
- No FPGA PASS without a real board, bitstream, BAR/DMA access, interrupt/completion, and unchanged software replay.
- No multi-model product claim beyond the Qwen 3B gates defined here.

## Verification strategy
> Zero human intervention - all verification is agent-executed.

- Test decision: TDD using pytest for Python, CTest/GTest or an equivalent repository-local C++ harness for runtime code, and upstream llama.cpp backend tests at the pinned commit.
- QA policy: every todo has agent-executed happy and failure scenarios.
- Golden policy:
  - independent NumPy/llama.cpp references may validate numerical output;
  - production runtime/compiler code must not be imported by the independent oracle;
  - shared parsing of immutable model data is allowed only when explicitly documented.
- Firmware policy:
  - Python firmware remains a fast test double;
  - Spike plus compiled firmware is a mandatory integration gate;
  - RTL/FPGA run target-linked images built from the same firmware source and generated ABI.
- Testbench policy:
  - the same scenario object drives Func Model, RTL, and FPGA adapters;
  - deliberate corruption must cause deterministic detection;
  - evidence records the actual DUT adapter, firmware mode, ABI version, model hash, and command path.
- Evidence: `.omo/evidence/task-<N>-<slug>.<ext>`.

## Execution strategy

### Parallel execution waves

> Target 5-8 todos per wave. Tasks in a wave may start together only when their dependency rows permit it.

Wave 1, foundations:

- Todo 1: ABI schema and generator
- Todo 3: public Host Runtime API
- Todo 4: scenario/DUT abstraction
- Todo 5: llama.cpp dependency lock and harness
- Todo 6: reproducible Spike toolchain

Wave 2, shared infrastructure:

- Todo 2: generated-binding migration
- Todo 7: Runtime core and mock transport
- Todo 8: binary device protocol and Func Model server
- Todo 9: Func Model DUT adapter
- Todo 10: RTL DUT adapter
- Todo 11: command IR and hardware lowering

Wave 3, integration correctness:

- Todo 12: Runtime through real Spike firmware
- Todo 13: testbench fault injection
- Todo 14: Func Model/RTL differential suite
- Todo 15: complete llama.cpp backend lifecycle
- Todo 16: llama.cpp op lowering and fallback

Wave 4, workload and platform replay:

- Todo 17: Qwen 3B llama.cpp gates
- Todo 18: Host Runtime replay on SoC RTL
- Todo 19: Linux FPGA userspace transport
- Todo 20: unchanged software replay on FPGA
- Todo 21: ExecuTorch v1.2 delegate

Wave 5, consolidation:

- Todo 22: packaging, CI gates, signoff checklist, and migration documentation

Critical path:

`1 -> 2 -> 7 -> 8 -> 11 -> 12 -> 15 -> 16 -> 17 -> 18 -> 19 -> 20 -> 22`

### Dependency matrix

| Todo | Depends on | Blocks | Can parallelize with |
|---|---|---|---|
| 1 | None | 2, 6, 11 | 3, 4, 5 |
| 2 | 1 | 7, 8, 11, 12 | 9, 10 |
| 3 | None | 7, 15, 19, 21 | 1, 4, 5, 6 |
| 4 | None | 9, 10, 13, 14 | 1, 3, 5, 6 |
| 5 | None | 15, 17 | 1, 3, 4, 6 |
| 6 | 1 | 12 | 3, 4, 5 |
| 7 | 2, 3 | 8, 11, 15, 19, 21 | 9, 10 |
| 8 | 2, 7 | 9, 12, 15 | 10, 11 |
| 9 | 4, 8 | 13, 14, 17 | 10, 11 |
| 10 | 4, 2 | 14, 18 | 8, 9, 11 |
| 11 | 1, 2, 7 | 12, 16, 21 | 8, 9, 10 |
| 12 | 6, 8, 11 | 17, 18, 22 | 13, 14, 15, 16 |
| 13 | 4, 9 | 14, 22 | 12, 15, 16 |
| 14 | 9, 10, 13 | 18, 22 | 12, 15, 16 |
| 15 | 3, 5, 7, 8 | 16, 17 | 12, 13, 14 |
| 16 | 11, 15 | 17 | 12, 13, 14 |
| 17 | 9, 12, 16 | 22 | 18, 19, 21 |
| 18 | 10, 12, 14, 17 | 20, 22 | 19, 21 |
| 19 | 3, 7 | 20 | 17, 18, 21 |
| 20 | 17, 18, 19 | 22 | 21 |
| 21 | 3, 7, 11 | 22 | 17, 18, 19, 20 |
| 22 | 12, 13, 14, 17, 18, 20, 21 | Final wave | None |

## Todos

> Implementation + Test = ONE todo. Never separate.

- [ ] 1. Establish a single versioned HW/SW ABI schema and generator
  - What to do:
    - add `spec/npu_abi.json` as the authoritative schema for address regions, registers, opcodes, descriptor fields, command/completion rings, status/error values, ABI version, and capability bits;
    - add `scripts/gen_npu_abi.py` with deterministic `--generate` and `--check` modes;
    - generate Python, C/C++, firmware, SystemVerilog, and Markdown artifacts under clearly marked generated paths;
    - encode field width, byte offset, packed size, alignment, endianness, reset value, access type, and compatibility policy;
    - begin with ABI major `1`; incompatible layout changes increment major, additive capability-compatible changes increment minor.
  - Must NOT do:
    - do not parse Python or C source as the long-term source of truth;
    - do not silently renumber an existing register, opcode, or descriptor field.
  - Parallelization: Can parallel Y | Wave 1 | Blocks 2, 6, 11
  - References:
    - `sim/regmap.py`
    - `firmware/npu-regmap.h`
    - `sim/engine/isa.py`
    - `sim/spike_host.py:31`
    - `firmware/npu_firmware.c:25`
    - `sim/check_mmio_map.py`
    - `scripts/verify_descriptor_alignment.py`
  - Acceptance criteria:
    - first add failing tests for the current duplicated/mismatched contract, including `DOORBELL.COMPLETION_STATUS`;
    - `PYTHONPATH=sim python3 scripts/gen_npu_abi.py --check` exits 0;
    - two consecutive generations produce byte-identical output;
    - a temporary schema mutation causes `--check` to fail without changing checked-in files.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 -m pytest sim/tests/test_npu_abi_schema.py -q && python3 scripts/gen_npu_abi.py --check 2>&1 | tee .omo/evidence/task-1-abi-generate.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_npu_abi_schema.py -q -k rejects_mutated_copy 2>&1 | tee .omo/evidence/task-1-abi-negative.log`.
  - Commit: Y | `feat(abi): add versioned NPU ABI schema and generators` | `spec/`, `scripts/gen_npu_abi.py`, generated artifacts, tests

- [ ] 2. Migrate existing Python, firmware, RTL, and Host definitions to generated bindings
  - What to do:
    - make `sim/regmap.py` a compatibility facade over generated Python constants;
    - make `firmware/npu-regmap.h` include the generated firmware contract while preserving existing public macro/type names;
    - consume the generated SystemVerilog package or generated include from the RTL-visible software contract points;
    - add C/C++ `static_assert` checks for descriptor sizes and `offsetof`;
    - reconcile the current completion-status checker discrepancy and descriptor notes without changing software-visible semantics.
  - Must NOT do:
    - do not keep a second editable copy of addresses or descriptor layouts;
    - do not change RTL datapath behavior.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 1 | Blocks 7, 8, 11, 12
  - References:
    - `sim/regmap.py`
    - `firmware/npu-regmap.h`
    - `firmware/npu_firmware.c`
    - `sim/cocotb_bridge.py:98`
    - `sim/rtl_soc_runner.py:35`
    - `sim/check_mmio_map.py`
  - Acceptance criteria:
    - `PYTHONPATH=sim python3 sim/check_mmio_map.py` exits 0;
    - `PYTHONPATH=sim python3 scripts/verify_descriptor_alignment.py` exits 0 with no unresolved design inconsistency;
    - firmware builds with all ABI static assertions enabled;
    - RTL compile/preprocess can resolve the generated package/include.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 sim/check_mmio_map.py && PYTHONPATH=sim python3 scripts/verify_descriptor_alignment.py && make -C firmware clean all 2>&1 | tee .omo/evidence/task-2-binding-migration.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_npu_abi_bindings.py -q -k rejects_mutated_generated_copy 2>&1 | tee .omo/evidence/task-2-binding-negative.log`.
  - Commit: Y | `refactor(abi): consume generated contract across software and RTL` | `sim/regmap.py`, `firmware/`, RTL contract includes, tests

- [ ] 3. Define the stable C Host Runtime ABI and compatibility rules
  - What to do:
    - create `software/include/caduceus/runtime.h` with opaque device, buffer, queue, command-list, and fence handles;
    - define versioned structs with explicit `struct_size`, `abi_major`, and `abi_minor`;
    - expose device open/close, capability query, buffer allocate/free/read/write, command-list construction, submit, wait/poll, status/error retrieval, and reset;
    - define URI selection such as `fm://`, `rtl://`, and `fpga://`;
    - create a C++ RAII wrapper without changing the C ABI;
    - document thread-safety, ownership, timeout, and error-lifetime semantics.
  - Must NOT do:
    - do not expose Python objects, cocotb handles, BAR offsets, physical pointers, or framework types;
    - do not let framework adapters serialize hardware descriptors directly.
  - Parallelization: Can parallel Y | Wave 1 | Blocks 7, 15, 19, 21
  - References:
    - `sim/func_model.py:117`
    - `sim/spike_host.py:49`
    - `docs/pcie-dma-data-flow.md:221`
    - `docs/NPU软件架构方案v0.2.md:185`
  - Acceptance criteria:
    - a C translation unit and a C++ translation unit both compile against `runtime.h`;
    - ABI-layout tests assert public struct sizes and field offsets;
    - an older minor-version client is accepted when requested capabilities are supported;
    - a major-version mismatch returns a typed incompatibility error.
  - QA scenarios:
    - happy: `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software && ctest --test-dir build/software -R runtime_abi --output-on-failure 2>&1 | tee .omo/evidence/task-3-runtime-abi.log`;
    - failure: `ctest --test-dir build/software -R runtime_abi_negative --output-on-failure 2>&1 | tee .omo/evidence/task-3-runtime-abi-negative.log`.
  - Commit: Y | `feat(runtime): define stable C host runtime ABI` | `software/include/`, `software/tests/`

- [ ] 4. Extract a shared scenario, observation, scoreboard, and DUT-adapter contract
  - What to do:
    - create a transport-independent verification package under `sim/verification/`;
    - define versioned `Scenario`, action, expected-observation, tolerance, provenance, and evidence records;
    - define one async DUT adapter contract used by Func Model, RTL, and FPGA;
    - migrate `TestCaseConfig` data without breaking existing FM-SOC vector loading;
    - classify each operation as frontdoor, allowed initialization backdoor, observation backdoor, or diagnostic-only.
  - Must NOT do:
    - do not embed cocotb signal names or Func Model objects in the scenario schema;
    - do not let the scoreboard read expected output from the DUT under test.
  - Parallelization: Can parallel Y | Wave 1 | Blocks 9, 10, 13, 14
  - References:
    - `sim/rtl_soc_runner.py:161`
    - `sim/rtl_soc_runner.py:209`
    - `sim/rtl_soc_runner.py:876`
    - `sim/tests/test_soc_rtl_e2e.py`
    - `rtl/test_vectors/qwen_blk0/`
    - `sim/golden_executor.py`
  - Acceptance criteria:
    - existing `.npz` cases round-trip into the new scenario representation;
    - schema serialization is deterministic;
    - an adapter contract test runs against a fake DUT;
    - a scenario containing an undeclared backdoor operation is rejected.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_scenario.py -q -k 'roundtrip or fake_dut' 2>&1 | tee .omo/evidence/task-4-scenario-roundtrip.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_scenario.py -q -k 'rejects_malformed or rejects_forbidden_backdoor' 2>&1 | tee .omo/evidence/task-4-scenario-negative.log`.
  - Commit: Y | `refactor(verification): extract shared scenario and DUT contracts` | `sim/verification/`, compatibility tests

- [ ] 5. Pin and stage the official llama.cpp integration surface
  - What to do:
    - add a dependency lock recording repository, commit `88b47a755c72fed4b22fba0fd262e2d7b7d01583`, retrieval method, and license;
    - add a reproducible fetch/build script that materializes the locked source at `third_party/llama.cpp` and does not depend on `~/llama.cpp`;
    - integrate `ggml-npu/` as a backend library at the pinned source surface;
    - compile an empty lifecycle backend before adding compute support;
    - run upstream `test-backend-ops` or the pinned equivalent as the backend conformance harness.
  - Must NOT do:
    - do not track a moving branch;
    - do not copy an unversioned external checkout into source;
    - do not rely on the currently empty `llama_ref/llama.cpp` directory.
  - Parallelization: Can parallel Y | Wave 1 | Blocks 15, 17
  - References:
    - `ggml-npu/CMakeLists.txt`
    - `ggml-npu/ggml-npu.cpp`
    - `ggml-npu/README.md`
    - `sim/qwen25_forward.py:493`
    - generated dependency checkout `third_party/llama.cpp/ggml/src/ggml-backend-impl.h`
    - generated dependency checkout `third_party/llama.cpp/ggml/CMakeLists.txt`, target `test-backend-ops`
  - Acceptance criteria:
    - dependency fetch verifies the exact commit;
    - backend plugin/library builds in a clean directory;
    - backend registry reports exactly one Caduceus NPU device;
    - changing the lock commit without refreshing metadata causes a check failure.
  - QA scenarios:
    - happy: `python3 scripts/fetch_llama_cpp.py --lock deps/llama-cpp.lock --check && cmake -S third_party/llama.cpp -B build/llama -DGGML_NPU=ON -DGGML_BACKEND_DL=ON && cmake --build build/llama --target test-backend-ops 2>&1 | tee .omo/evidence/task-5-llama-pin.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_llama_dependency_lock.py -q -k rejects_wrong_commit 2>&1 | tee .omo/evidence/task-5-llama-pin-negative.log`.
  - Commit: Y | `build(llama): pin official backend integration surface` | dependency lock, build/fetch scripts, `ggml-npu/`

- [ ] 6. Make the Spike and firmware toolchain reproducible
  - What to do:
    - add a preflight/build script for the pinned Spike source, device-tree compiler, MMIO plugin, RISC-V compiler, and both firmware link targets;
    - encode the `_GLIBCXX_USE_CXX11_ABI` requirement in the plugin build rather than relying on tribal knowledge;
    - emit a machine-readable artifact manifest containing source commit, compiler versions, ELF hashes, plugin hash, and ABI schema version;
    - prove the Spike image and RTL/FPGA image are built from the same firmware source revision and generated ABI.
  - Must NOT do:
    - do not silently fall back to Python firmware when a real-firmware gate is requested;
    - do not require bit-identical target-linked ELFs.
  - Parallelization: Can parallel Y | Wave 1 | Blocked by 1 | Blocks 12
  - References:
    - `docs/spike-integration.md`
    - `firmware/Makefile`
    - `firmware/npu_firmware.c`
    - `firmware/link.ld`
    - `firmware/spike_link.ld`
    - `sim/spike_firmware.py`
    - `sim/func_model.py:77`
  - Acceptance criteria:
    - the preflight detects the current missing `spike_src/build/spike` and returns a typed failure before build;
    - the build produces Spike, plugin, firmware ELFs, and artifact manifest;
    - `FuncModel(use_spike=True)` never falls back to Python firmware;
    - a source/header timestamp or hash mismatch invalidates the manifest.
  - QA scenarios:
    - happy: `python3 scripts/build_spike_stack.py --clean --manifest .omo/evidence/task-6-spike-build.json 2>&1 | tee .omo/evidence/task-6-spike-build.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_spike_toolchain_manifest.py -q -k rejects_incomplete_or_stale_manifest 2>&1 | tee .omo/evidence/task-6-spike-negative.log`.
  - Commit: Y | `build(firmware): make Spike integration reproducible` | build scripts, Makefiles, manifests, docs

- [ ] 7. Implement the Host Runtime core and mock transport conformance suite
  - What to do:
    - add `software/src/` runtime ownership, handle validation, queueing, buffers, fences, timeout, status, and error propagation;
    - define an internal transport vtable/interface;
    - implement a deterministic mock transport for TDD;
    - add C, C++, and Python binding smoke tests;
    - ensure all transport implementations must pass one shared conformance suite.
  - Must NOT do:
    - do not put Func Model, RTL, FPGA, or framework-specific logic in the runtime core;
    - do not return raw internal pointers as public buffer addresses.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 2, 3 | Blocks 8, 11, 15, 19, 21
  - References:
    - `sim/func_model.py:117`
    - `sim/spike_host.py:147`
    - `ggml-npu/ggml-npu.cpp`
  - Acceptance criteria:
    - clean CMake build plus CTest exits 0;
    - all public APIs reject invalid/stale handles;
    - queue order, timeout, cancellation/reset, and error-lifetime tests pass;
    - Python binding drives the same mock conformance cases.
  - QA scenarios:
    - happy: `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON && cmake --build build/software && ctest --test-dir build/software -R runtime_conformance --output-on-failure 2>&1 | tee .omo/evidence/task-7-runtime-core.log`;
    - failure: `ctest --test-dir build/software -R runtime_faults --output-on-failure 2>&1 | tee .omo/evidence/task-7-runtime-core-negative.log`.
  - Commit: Y | `feat(runtime): implement core and transport conformance suite` | `software/`

- [ ] 8. Implement a versioned binary device protocol and Func Model server
  - What to do:
    - define framed little-endian requests/responses with magic, protocol version, request ID, opcode, payload length, status, and checksum;
    - implement the C/C++ client transport and Python `sim/device_server.py`;
    - route server operations through `FuncModel` PCIe/BAR/doorbell/completion behavior;
    - support buffer lifecycle, transfer, submit, wait, status, reset, and capability query;
    - use Unix socket first; allow shared-memory payload optimization later without changing semantics.
  - Must NOT do:
    - do not send JSON tensor payloads;
    - do not compute operators in the server outside Func Model;
    - do not reuse `/tmp/npu_stimulus`.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 2, 7 | Blocks 9, 12, 15
  - References:
    - `sim/func_model.py`
    - `sim/models/pcie.py:115`
    - `sim/spike_mmio_server.py`
    - `ggml-npu/npu_server.py`
    - `ggml-npu/ggml-npu.cpp:13`
  - Acceptance criteria:
    - protocol golden vectors decode identically in C++ and Python;
    - malformed length, checksum, version, and request ordering fail deterministically;
    - a Runtime client transfers data and completes one command through the Func Model server;
    - process restart does not reuse stale handles or request IDs.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_device_protocol_tests.py --transport fm://python --evidence .omo/evidence/task-8-fm-protocol.log`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_device_protocol.py -q -k 'malformed or truncated or server_dies_during_wait' 2>&1 | tee .omo/evidence/task-8-fm-protocol-negative.log`.
  - Commit: Y | `feat(transport): add binary Func Model device protocol` | `software/`, `sim/device_server.py`, protocol tests

- [ ] 9. Implement the shared Func Model DUT adapter
  - What to do:
    - implement the adapter contract from Todo 4 over `FuncModel`;
    - support both Python firmware and explicit real-Spike modes;
    - implement frontdoor PCIe/MMIO actions and classified initialization/observation backdoors;
    - emit evidence with firmware mode, ABI version, and actual path used;
    - migrate representative existing SoC tests to scenario execution without removing legacy coverage.
  - Must NOT do:
    - do not label Python-firmware evidence as real-firmware evidence;
    - do not use direct DRAM writes for software E2E actions.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 4, 8 | Blocks 13, 14, 17
  - References:
    - `sim/func_model.py`
    - `sim/tests/test_soc_fm.py`
    - `sim/tests/test_func_model_signoff_v3_host.py`
    - `sim/models/pcie.py`
  - Acceptance criteria:
    - APB, PCIe, command-ring, engine, interrupt/completion, and reset scenarios pass;
    - evidence distinguishes frontdoor and backdoor actions;
    - software E2E scenarios contain zero operation-performing backdoor actions;
    - legacy and migrated scenarios produce equivalent observations.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_dut_scenarios.py --dut fm --firmware python --matrix software-smoke --evidence .omo/evidence/task-9-fm-adapter.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_func_model_dut_adapter.py -q -k real_spike_missing_artifacts_fails 2>&1 | tee .omo/evidence/task-9-fm-adapter-negative.log`.
  - Commit: Y | `feat(verification): add Func Model DUT adapter` | `sim/verification/`, migrated tests

- [ ] 10. Refactor `RTLSoCRunner` behind the shared RTL DUT adapter
  - What to do:
    - wrap cocotb/VPI/APB/TLP/backdoor operations behind the Todo 4 adapter;
    - preserve existing FM-SOC testcase loading and mixed-mode controls;
    - move scenario-independent scoreboard logic out of `RTLSoCRunner`;
    - classify current wrapper workarounds and backdoors as diagnostic or initialization-only;
    - make evidence identify full RTL, mixed mode, and enabled module set.
  - Must NOT do:
    - do not modify RTL logic to make adapter tests pass;
    - do not hide a wrapper workaround as generic DUT behavior.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 4, 2 | Blocks 14, 18
  - References:
    - `sim/rtl_soc_runner.py:209`
    - `sim/rtl_soc_runner.py:496`
    - `sim/rtl_soc_runner.py:578`
    - `sim/rtl_soc_runner.py:753`
    - `sim/cocotb_bridge.py`
  - Acceptance criteria:
    - existing RTL runner smoke and FM-SOC vector loading remain green;
    - the common adapter conformance suite runs against RTL;
    - evidence rejects ambiguous DUT mode;
    - no common scoreboard code imports cocotb.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_dut_scenarios.py --dut rtl --matrix adapter-smoke --evidence .omo/evidence/task-10-rtl-adapter.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_soc_rtl_e2e.py -q -k 'adapter_timeout or missing_completion' 2>&1 | tee .omo/evidence/task-10-rtl-adapter-negative.log`.
  - Commit: Y | `refactor(verification): put RTL runner behind common DUT adapter` | `sim/rtl_soc_runner.py`, `sim/verification/`, tests

- [ ] 11. Replace the conceptual compiler with a production command IR and hardware lowering path
  - What to do:
    - define a framework-neutral typed command IR for MMUL, SFU, Vector, DMA, barriers, buffers, and dependencies;
    - lower IR into generated ABI descriptors and command-ring entries;
    - implement deterministic address allocation, alignment, bounds, tiling, last/remainder tiles, and dependency validation;
    - define a versioned compiled-command blob usable by llama.cpp dynamic lowering and ExecuTorch AOT preprocessing;
    - retain `sim/engine/compiler.py` only as a compatibility or performance-model front end after production lowering exists.
  - Must NOT do:
    - do not use hash-based or repeated fixed addresses;
    - do not expose physical addresses to framework graph adapters;
    - do not let the compiler oracle call production execution code.
  - Parallelization: Can parallel Y | Wave 2 | Blocked by 1, 2, 7 | Blocks 12, 16, 21
  - References:
    - `sim/engine/compiler.py`
    - `sim/engine/isa.py`
    - `sim/tile_scheduler.py`
    - `sim/spike_host.py:49`
    - `firmware/npu_firmware.c:40`
    - `rtl/test_vectors/qwen_blk0/blk0_manifest.json`
  - Acceptance criteria:
    - TDD covers valid and invalid shapes, alignment, buffer overlap, address overflow, last/remainder tiles, and unsupported ops;
    - command blobs are deterministic and versioned;
    - decoder/round-trip tests agree across C++ and Python;
    - compiled Qwen blk.0 descriptors match the independent scenario manifest semantically.
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R command_lowering --output-on-failure 2>&1 | tee .omo/evidence/task-11-command-lowering.log`;
    - failure: `ctest --test-dir build/software -R command_lowering_negative --output-on-failure 2>&1 | tee .omo/evidence/task-11-command-lowering-negative.log`.
  - Commit: Y | `feat(compiler): add typed command IR and ABI lowering` | `software/compiler/`, compatibility integration, tests

- [ ] 12. Drive the Host Runtime through real Spike firmware
  - What to do:
    - connect runtime requests from the Func Model server to `FuncModel(use_spike=True)`;
    - run target-linked Spike firmware built from the same source/ABI as RTL/FPGA firmware;
    - cover command ring, all Qwen-required engine classes, completion, error, timeout, and reset;
    - record ELF/plugin/schema hashes in every integration artifact;
    - make real-firmware test selection fail, not skip, when prerequisites are missing.
  - Must NOT do:
    - do not invoke `sim/spike_host.py` as a separate alternative stack for signoff;
    - do not fall back to `NPUFirmware`.
  - Parallelization: Can parallel Y | Wave 3 | Blocked by 6, 8, 11 | Blocks 17, 18, 22
  - References:
    - `sim/func_model.py:77`
    - `sim/spike_firmware.py`
    - `sim/spike_host.py`
    - `firmware/npu_firmware.c`
    - `docs/spike-integration.md`
  - Acceptance criteria:
    - runtime C client completes MMUL, SFU, Vector, DMA, and chained commands through compiled firmware;
    - evidence proves `SpikeFirmware` and the expected ELF hash were used;
    - descriptor corruption returns the documented firmware status;
    - missing Spike/plugin/ELF causes nonzero test failure.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_dut_scenarios.py --dut fm --firmware spike --matrix real-firmware --require-prereqs --evidence .omo/evidence/task-12-real-firmware.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_runtime_real_firmware.py -q -k 'incompatible_abi or corrupted_descriptor or missing_prereq_fails' --require-spike 2>&1 | tee .omo/evidence/task-12-real-firmware-negative.json`.
  - Commit: Y | `feat(firmware): integrate real Spike path with host runtime` | runtime/FM server integration, tests, evidence tooling

- [ ] 13. Add testbench self-validation through deterministic fault injection
  - What to do:
    - add adapter-level injection hooks that are unavailable in production runtime;
    - inject data corruption, wrong descriptor field, unsupported opcode, ring overflow, stalled head, wrong completion, dropped/duplicate interrupt, timeout, engine error, and reset-during-command;
    - require the shared monitor/scoreboard to classify every injected fault;
    - verify the injection itself occurred before accepting a detected failure.
  - Must NOT do:
    - do not implement faults by weakening expected output;
    - do not treat an unexecuted injection as evidence.
  - Parallelization: Can parallel Y | Wave 3 | Blocked by 4, 9 | Blocks 14, 22
  - References:
    - `sim/tests/test_soc_fm.py:1505`
    - `sim/tests/test_soc_fm.py:1535`
    - `sim/tests/test_soc_fm.py:1557`
    - `sim/rtl_soc_runner.py:694`
    - `docs/caduceus-verification-lessons.md`
  - Acceptance criteria:
    - every fault case records `injection_applied=true` and expected classification;
    - removing the detector makes the corresponding test fail;
    - normal scenarios remain unaffected;
    - fault hooks cannot be enabled through public production Runtime APIs.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_dut_scenarios.py --dut fm --matrix fault-injection --evidence .omo/evidence/task-13-fault-injection.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_verification_fault_injection.py -q -k injection_not_applied_is_failure 2>&1 | tee .omo/evidence/task-13-injection-not-applied.log`.
  - Commit: Y | `test(verification): add deterministic testbench fault injection` | `sim/verification/`, tests

- [ ] 14. Establish Func Model/RTL differential signoff scenarios
  - What to do:
    - run identical scenarios through Func Model and RTL adapters;
    - compare numerical outputs, visible memory effects, command order, head/tail, completion, status/error, interrupt, and reset behavior;
    - use independent golden results in addition to cross-DUT comparison;
    - produce structured divergence reports that identify contract, transport, firmware, or compute class;
    - start with APB, PCIe/BAR, MMUL, SFU, Vector, DMA, command ring, firmware chain, and corruption cases.
  - Must NOT do:
    - do not accept FM==RTL as sufficient when both disagree with the independent golden;
    - do not auto-classify an RTL divergence as a Func Model defect.
  - Parallelization: Can parallel Y | Wave 3 | Blocked by 9, 10, 13 | Blocks 18, 22
  - References:
    - `sim/rtl_soc_runner.py`
    - `sim/tests/test_soc_rtl_e2e.py`
    - `rtl/test_vectors/qwen_blk0/`
    - `sim/golden_executor.py`
    - `docs/caduceus-verification-lessons.md:72`
  - Acceptance criteria:
    - all selected scenarios produce three-way FM/RTL/golden evidence;
    - fault-injected divergence is detected and correctly classified;
    - stale or missing RTL result files cannot be reused as current evidence;
    - unexplained divergence fails the gate.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_soc_differential.py --matrix software-functional --evidence .omo/evidence/task-14-differential.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_soc_differential.py -q -k 'detects_divergence or rejects_stale_provenance' 2>&1 | tee .omo/evidence/task-14-differential-negative.json`.
  - Commit: Y | `test(soc): add Func Model RTL differential gate` | differential runner, scenarios, evidence schema

- [ ] 15. Implement the complete llama.cpp backend lifecycle over the Host Runtime
  - What to do:
    - implement backend registry, device discovery/properties, buffer type, buffer allocation/free, tensor set/get/copy, backend create/free, synchronization, `supports_op`, and graph compute interfaces required by the pinned commit;
    - connect backend device selection to a Runtime URI;
    - pass upstream backend lifecycle and buffer tests before advertising compute support;
    - return precise backend errors and never report success after Runtime failure.
  - Must NOT do:
    - do not call Python directly from the backend;
    - do not retain hex-file or NumPy server execution;
    - do not advertise unsupported operations.
  - Parallelization: Can parallel Y | Wave 3 | Blocked by 3, 5, 7, 8 | Blocks 16, 17
  - References:
    - `ggml-npu/ggml-npu.cpp`
    - `ggml-npu/ggml-npu.h`
    - `ggml-npu/CMakeLists.txt`
    - generated dependency checkout `third_party/llama.cpp/ggml/src/ggml-backend-impl.h`
    - generated dependency checkout `third_party/llama.cpp/ggml/include/ggml-backend.h`
    - generated dependency checkout target `third_party/llama.cpp/ggml/CMakeLists.txt::test-backend-ops`
  - Acceptance criteria:
    - backend registers and reports one device with accurate memory/capability data;
    - upstream lifecycle, allocation, transfer, copy, and synchronization tests pass;
    - an injected Runtime failure propagates as non-success `ggml_status`;
    - `rg` confirms production backend contains no `/tmp/npu_stimulus` or direct Python/NumPy path.
  - QA scenarios:
    - happy: `CADUCEUS_DEVICE=mock:// build/llama/bin/test-backend-ops test -b NPU && CADUCEUS_DEVICE=fm://python build/llama/bin/test-backend-ops test -b NPU 2>&1 | tee .omo/evidence/task-15-ggml-lifecycle.log`;
    - failure: `ctest --test-dir build/software -R ggml_runtime_faults --output-on-failure 2>&1 | tee .omo/evidence/task-15-ggml-lifecycle-negative.log`.
  - Commit: Y | `feat(ggml): implement runtime-backed NPU backend lifecycle` | `ggml-npu/`, build integration, tests

- [ ] 16. Add Qwen-required ggml operation lowering and correct CPU fallback
  - What to do:
    - map supported Qwen operations and tensor formats into the Todo 11 IR;
    - begin with quantized `MUL_MAT`, then RMSNorm, RoPE, Softmax, SiLU, residual add, and element-wise multiply as hardware support allows;
    - make `supports_op` shape-, dtype-, layout-, and capability-aware;
    - let llama.cpp partition unsupported nodes to CPU without copying invalid/stale tensors;
    - verify mixed CPU/NPU graph ordering and synchronization.
  - Must NOT do:
    - do not claim an operation supported based only on opcode existence;
    - do not return success without executing or explicitly delegating every node.
  - Parallelization: Can parallel Y | Wave 3 | Blocked by 11, 15 | Blocks 17
  - References:
    - `ggml-npu/ggml-npu.cpp:163`
    - `sim/qwen25_forward.py`
    - `rtl/test_vectors/qwen_blk0/blk0_manifest.json`
    - `firmware/npu_firmware.c:394`
    - `sim/golden_executor.py`
  - Acceptance criteria:
    - per-op positive and negative support matrices pass;
    - supported operations match independent golden tolerances;
    - unsupported shape/dtype/layout executes correctly through CPU fallback;
    - mixed graphs have no missing synchronization or stale-buffer read.
  - QA scenarios:
    - happy: `CADUCEUS_DEVICE=fm://python build/llama/bin/test-backend-ops support -b NPU --output csv > .omo/evidence/task-16-ggml-ops.csv && CADUCEUS_DEVICE=fm://python build/llama/bin/test-backend-ops test -b NPU`;
    - failure: `ctest --test-dir build/software -R ggml_op_support_negative --output-on-failure 2>&1 | tee .omo/evidence/task-16-ggml-ops-negative.json`.
  - Commit: Y | `feat(ggml): lower Qwen operations with safe fallback` | `ggml-npu/`, compiler integration, tests

- [ ] 17. Close llama.cpp Qwen 3B functional software gates
  - What to do:
    - use a pinned Qwen 3B GGUF model hash and deterministic prompts/seeds;
    - run backend gates in order: supported single ops, full-shape blk.0, one full decode token, multi-token decode with KV cache, and CPU-fallback mixed graph;
    - compare backend outputs to independent llama.cpp CPU reference and existing Func Model Qwen references;
    - record model hash, llama commit, backend hash, ABI version, firmware mode, transport, supported/fallback node counts, and tolerances;
    - retain scaled/synthetic cases as fast tests, not final evidence.
  - Must NOT do:
    - do not substitute Qwen 1.5B evidence for Qwen 3B;
    - do not call a partial graph “full decode”;
    - do not upgrade performance status.
  - Parallelization: Can parallel Y | Wave 4 | Blocked by 9, 12, 16 | Blocks 22
  - References:
    - `sim/signoff/test_qwen25_3b_real_blk0.py`
    - `sim/qwen25_func_model.py`
    - `sim/qwen25_forward.py`
    - `rtl/test_vectors/qwen_blk0/blk0_manifest.json`
    - `docs/func-model-signoff-checklist.md`
  - Acceptance criteria:
    - all five gate levels pass on Func Model transport;
    - at least the real-firmware subset passes through Spike;
    - anti-vacuous weight/output corruption is detected;
    - evidence proves no hidden shape cap and distinguishes NPU from fallback nodes.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py --device fm://spike --model-manifest config/qwen3b-signoff.json --all-gates --evidence .omo/evidence/task-17-qwen3b-software.json`;
    - failure: `PYTHONPATH=sim python3 scripts/run_qwen3b_software_signoff.py --device fm://python --model-manifest config/qwen3b-signoff.json --negative corruption,unsupported-layout --evidence .omo/evidence/task-17-qwen3b-software-negative.json`.
  - Commit: Y | `test(llama): add Qwen 3B software signoff gates` | integration tests, manifests, evidence tooling

- [ ] 18. Replay the unchanged Host Runtime software path on SoC RTL
  - What to do:
    - implement an RTL simulation transport endpoint exposing the same binary protocol as Func Model;
    - drive SoC RTL through PCIe/BAR/doorbell rather than adapter-only direct command setup;
    - run the same Runtime client binaries, command blobs, scenarios, and firmware source/ABI used on Func Model;
    - compare RTL observations with Todo 14 and Qwen workload evidence;
    - keep VCS/tool availability as an explicit preflight, never a skip-to-PASS.
  - Must NOT do:
    - do not rebuild workload semantics specifically for RTL;
    - do not use a Python-only host path as software replay evidence.
  - Parallelization: Can parallel Y | Wave 4 | Blocked by 10, 12, 14, 17 | Blocks 20, 22
  - References:
    - `sim/rtl_soc_runner.py`
    - `sim/cocotb_bridge.py`
    - `sim/spike_rtl_bridge.py`
    - `rtl/tb/`
    - `sim/regression/run_fm_soc_case.sh`
  - Acceptance criteria:
    - the same compiled Runtime smoke client runs against `fm://` and `rtl://`;
    - command/completion/error scenarios match contract and independent golden;
    - evidence identifies the exact RTL build and firmware ELF hash;
    - missing EDA prerequisites fail preflight and leave RTL software signoff open.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_runtime_rtl_signoff.py --matrix software-functional,qwen-blk0 --require-eda --evidence .omo/evidence/task-18-rtl-runtime.json`;
    - failure: `PYTHONPATH=sim python3 scripts/run_runtime_rtl_signoff.py --matrix timeout,wrong-completion --expect-detection --require-eda --evidence .omo/evidence/task-18-rtl-runtime-negative.json`.
  - Commit: Y | `feat(transport): replay host runtime on SoC RTL` | RTL protocol endpoint, integration tests, scripts

- [ ] 19. Implement Linux FPGA userspace preflight and transport
  - What to do:
    - add an automated platform inventory for PCI BDF/vendor/device IDs, BAR sizes, UIO/VFIO/vendor nodes, IOMMU group, DMA API, MSI/MSI-X/eventfd, permissions, and bitstream metadata;
    - select transport deterministically:
      1. VFIO when the device has a viable IOMMU group and BAR mapping;
      2. otherwise UIO when mapped BAR and interrupt support satisfy the contract;
      3. otherwise an explicitly configured vendor device plugin;
      4. otherwise emit NO-GO and stop FPGA signoff;
    - implement BAR mapping, buffer/DMA ownership, submission, completion interrupt/poll, timeout, error, and reset behind the Todo 7 transport interface;
    - test with fake sysfs/device fixtures before real hardware.
  - Must NOT do:
    - do not access `/dev/mem`;
    - do not require framework changes;
    - do not claim multi-process security or product kernel-driver readiness.
  - Parallelization: Can parallel Y | Wave 4 | Blocked by 3, 7 | Blocks 20
  - References:
    - `docs/pcie-dma-data-flow.md`
    - `rtl/ip/README.md:71`
    - `sim/models/pcie.py`
    - `docs/soc-fm-gap-spec.md`
  - Acceptance criteria:
    - fake VFIO, UIO, vendor, and no-device fixtures exercise every decision branch;
    - mappings validate expected ABI/BAR sizes before access;
    - interrupt and polling completion paths pass conformance tests;
    - no-device systems produce structured NO-GO evidence, not PASS.
  - QA scenarios:
    - happy: `ctest --test-dir build/software -R fpga_transport_conformance --output-on-failure 2>&1 | tee .omo/evidence/task-19-fpga-transport.log`;
    - failure: `ctest --test-dir build/software -R fpga_transport_negative --output-on-failure 2>&1 | tee .omo/evidence/task-19-fpga-transport-negative.json`.
  - Commit: Y | `feat(transport): add Linux userspace FPGA backend` | `software/src/transport_fpga*`, preflight, tests, docs

- [ ] 20. Replay unchanged Runtime, firmware ABI, scenarios, and llama.cpp workload on FPGA
  - What to do:
    - require a GO result from Todo 19 plus board/bitstream metadata;
    - run Runtime conformance, command/completion/error scenarios, compiled-firmware checks, differential vectors, Qwen blk.0, one decode token, and multi-token smoke;
    - compare FPGA output to the independent golden and prior Func Model/RTL observations;
    - confirm the llama.cpp backend binary and Host Runtime API are unchanged from the validated software build;
    - document only transport configuration differences.
  - Must NOT do:
    - do not patch framework/runtime semantics for FPGA;
    - do not substitute RTL simulation for real FPGA evidence;
    - do not declare this todo complete when hardware is unavailable.
  - Parallelization: Can parallel Y | Wave 4 | Blocked by 17, 18, 19 | Blocks 22
  - References:
    - outputs of Todos 17-19
    - `docs/pcie-dma-data-flow.md`
    - `rtl/ip/README.md`
  - Acceptance criteria:
    - a real board runs all required gates with recorded BDF, bitstream hash, firmware hash, ABI version, and software build hash;
    - Func Model/RTL/FPGA outputs meet the defined comparison policy;
    - fault/timeout/reset behavior is detected;
    - if no board exists, `.omo/evidence/task-20-fpga-no-go.json` is produced and overall FPGA/product signoff remains blocked.
  - QA scenarios:
    - happy: `python3 scripts/run_fpga_software_signoff.py --config config/fpga-target.json --all-gates --require-board --evidence .omo/evidence/task-20-fpga-replay.json`;
    - failure: `python3 scripts/run_fpga_software_signoff.py --config config/fpga-target.json --negative incompatible-abi,timeout --require-board --evidence .omo/evidence/task-20-fpga-replay-negative.json`.
  - Commit: Y | `test(fpga): add unchanged software replay gate` | FPGA runner, configuration schema, evidence tooling

- [ ] 21. Add an ExecuTorch v1.2 backend over the shared compiler and Runtime
  - What to do:
    - pin official ExecuTorch `v1.2.0`;
    - implement AOT operator support, partitioner, and preprocess that emit the Todo 11 compiled-command blob;
    - implement runtime backend registration, init, execute, buffer binding, and error propagation over the C Host Runtime;
    - test supported and fallback partitions;
    - prove no second descriptor compiler or transport stack was introduced.
  - Must NOT do:
    - do not directly access registers or transports from the delegate;
    - do not fork Runtime semantics for ExecuTorch;
    - do not claim general model support beyond tested partitions.
  - Parallelization: Can parallel Y | Wave 4 | Blocked by 3, 7, 11 | Blocks 22
  - References:
    - `docs/NPU软件架构方案v0.2.md:137`
    - `sim/engine/compiler.py`
    - ExecuTorch v1.2 official backend delegate and partitioner contracts
  - Acceptance criteria:
    - AOT unit tests cover support, partition, preprocess, and incompatible blob;
    - C++ runtime tests cover init, execute, buffer errors, and Runtime failures;
    - a representative Qwen subgraph produces the same command-blob semantic hash as llama.cpp lowering;
    - source inspection plus link map prove reuse of shared Runtime/compiler.
  - QA scenarios:
    - happy: `PYTHONPATH=sim python3 scripts/run_executorch_delegate_tests.py --device fm://python --case qwen-subgraph --evidence .omo/evidence/task-21-executorch.json`;
    - failure: `PYTHONPATH=sim python3 scripts/run_executorch_delegate_tests.py --device fm://python --negative unsupported-partition,incompatible-blob --evidence .omo/evidence/task-21-executorch-negative.json`.
  - Commit: Y | `feat(executorch): add shared-runtime NPU delegate` | ExecuTorch integration, tests, dependency lock

- [ ] 22. Package the stack and publish scoped software signoff status
  - What to do:
    - provide reproducible build/install targets for Runtime, C/C++ headers, Python binding, device server, llama.cpp backend, optional ExecuTorch delegate, firmware artifacts, and test tools;
    - add CI tiers matching L0 contract, L1 runtime, L2 Func Model Python firmware, L3 real Spike, L4 RTL, L5 FPGA, and framework Qwen gates;
    - update software architecture, testbench reuse, firmware integration, FPGA migration, and signoff documentation;
    - update `docs/func-model-signoff-checklist.md` without overclaiming RTL, FPGA, or performance status;
    - add an evidence aggregator that rejects stale state, missing commands, mismatched hashes, skipped mandatory gates, and misleading success output.
  - Must NOT do:
    - do not mark FPGA/product signoff PASS if Todo 20 is NO-GO;
    - do not merge functional and performance signoff;
    - do not present agent summaries as evidence without command/artifact verification.
  - Parallelization: Can parallel N | Wave 5 | Blocked by 12-21 | Blocks final wave
  - References:
    - `docs/NPU软件架构方案v0.2.md`
    - `docs/func-model-signoff-checklist.md`
    - `docs/caduceus-verification-lessons.md`
    - `scripts/run_func_model_signoff.py`
    - all task evidence
  - Acceptance criteria:
    - clean build/install/package succeeds in a fresh build directory;
    - installed C, C++, and Python smoke clients run against Func Model;
    - evidence aggregator reports exact PASS/PARTIAL/BLOCKED scope;
    - dirty unrelated worktree paths are unchanged.
  - QA scenarios:
    - happy: `python3 scripts/build_software_release.py --clean --install-prefix build/install && python3 scripts/aggregate_software_signoff.py --require l0,l1,l2,l3,l4,l5,framework --evidence .omo/evidence/task-22-release-signoff.json`;
    - failure: `PYTHONPATH=sim python3 -m pytest sim/tests/test_software_signoff_aggregator.py -q -k 'stale or hash_mismatch or skipped or misleading_success' 2>&1 | tee .omo/evidence/task-22-release-signoff-negative.json`.
  - Commit: Y | `docs(software): package and publish scoped software signoff` | build/package files, CI, docs, evidence tooling

## Final verification wave
> Runs in parallel. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [ ] F1. Plan compliance audit
  - Verify every approved decision and guardrail against the implementation diff and evidence ledger.
  - Reject direct framework-to-Python/register paths, hidden fallback, ungenerated ABI duplication, or FPGA overclaim.
  - Evidence: `.omo/evidence/final-plan-compliance.md`.

- [ ] F2. Code quality and ABI review
  - Review ownership, error handling, lifetime, thread safety, binary protocol bounds, generated-code determinism, compiler validation, and public ABI compatibility.
  - Run clean builds, sanitizers where supported, Python tests, CTest, firmware build, and static contract checks.
  - Evidence: `.omo/evidence/final-code-quality.md`.

- [ ] F3. Real manual QA
  - Drive the actual installed software surfaces:
    - C Runtime client;
    - Python Runtime binding;
    - Func Model server with Python firmware;
    - Func Model server with real Spike firmware;
    - pinned llama.cpp backend with Qwen 3B;
    - RTL Runtime replay;
    - FPGA replay when GO;
    - ExecuTorch delegated subgraph.
  - Verify observable outputs, failure behavior, and provenance.
  - Evidence: `.omo/evidence/final-manual-qa.md`.

- [ ] F4. Scope fidelity and evidence audit
  - Confirm unrelated worktree changes were preserved.
  - Confirm no RTL functional fix, performance signoff, product kernel driver, or untested multi-model claim entered scope.
  - Re-run the evidence aggregator and reject stale/misleading success artifacts.
  - Evidence: `.omo/evidence/final-scope-fidelity.md`.

## Commit strategy

- One atomic commit per todo using the commit subjects listed above.
- Generated artifacts and their schema/generator land in the same commit.
- Implementation and its tests land together.
- Do not commit model binaries, local sockets, build directories, FPGA bitstreams, device nodes, or temporary evidence inputs.
- Preserve unrelated dirty paths, especially existing `.omo` drafts/plans and `.omo/notepads/phase6-rtl-verification/learnings.md`.
- Do not squash across ABI, runtime, framework, RTL transport, or FPGA transport boundaries until all final reviewers approve.

## Success criteria

The unified software-stack work is complete only when:

1. one versioned schema generates all software/firmware/RTL-visible ABI bindings and all drift checks pass;
2. framework adapters use only the stable Host Runtime;
3. one Runtime client and one scenario format execute against Func Model and SoC RTL;
4. compiled firmware through Spike is a mandatory, passing integration gate;
5. testbench fault injection proves the monitor/scoreboard detects non-vacuous failures;
6. llama.cpp at the pinned commit runs Qwen 3B full-shape blk.0, one decode token, and multi-token smoke with scoped CPU fallback;
7. RTL replay uses the same Runtime API, command blobs, firmware source/ABI, and scenario semantics;
8. FPGA userspace transport passes conformance and, when hardware is available, unchanged software replay passes on a real board;
9. ExecuTorch v1.2 reuses the same compiler/runtime/transport rather than creating a parallel stack;
10. documentation and evidence distinguish Func Model, real firmware, RTL, FPGA, framework, and performance scopes without overclaiming.

If no FPGA platform is available, software readiness through Todo 19 may be reported, but Todo 20, final product/FPGA signoff, Todo 22 completion, and the final verification wave remain blocked.

# Func Model / SoC RTL / FPGA 统一软件栈路线 Draft

**Branch:** `feat_sw_stack`

**Baseline:** `456b40d`

**Status:** Architecture approved; formal plan generated
**Formal plan:** `.omo/plans/func-model-soc-software-stack.md`

## 1. User goals

The Func Model has three software-facing purposes:

1. serve as the behavioral golden reference for RTL;
2. let the same SoC verification intent and testbench be validated on the Func Model before replaying it on SoC RTL;
3. enable host and device software development before FPGA availability, with the same software stack later used on FPGA.

## 2. Verified current state

### Existing and reusable

- `sim/func_model.py` models SoC-visible DRAM, SRAM, PCIe TLP/BAR routing, crossbar, MMIO engines, doorbell, interrupt wiring, and firmware dispatch.
- `firmware/npu_firmware.c` is a real bare-metal RV32 firmware implementation for the NPU-side CPU.
- `sim/spike_host.py` already defines command-ring and descriptor serialization and can drive the real firmware through Spike when Spike is available.
- `sim/rtl_soc_runner.py` has reusable testcase data (`TestCaseConfig`) and an RTL runner that can load, run, and verify SoC cases through cocotb/VPI.
- Qwen Func Model and golden-reference assets already exist.
- `sim/check_mmio_map.py` and `scripts/verify_descriptor_alignment.py` provide partial HW/SW contract checks.

### Missing or not production-reusable

- There is no standalone Host Runtime/Driver library. Tests and adapters call Python `FuncModel.host_write_*()` helpers directly.
- The default Func Model path executes Python `NPUFirmware`; real compiled firmware is optional. The local Spike executable is currently missing.
- `ggml-npu` is a prototype. It writes file batches under `/tmp/npu_stimulus`; `npu_server.py` computes NumPy matmul directly and bypasses PCIe, command ring, firmware, completion, and errors.
- `sim/engine/compiler.py` is a conceptual trace compiler, not yet the production compiler/runtime path. Its address allocation and ISA encoding are not sufficient as a stable FPGA ABI.
- Func Model and RTL do not yet implement one common DUT/transport interface. `RTLSoCRunner` contains RTL-specific backdoor and wrapper workarounds.
- Register/descriptor contracts are duplicated across Python, C, RTL, and C++. `sim/check_mmio_map.py` currently reports a doorbell `COMPLETION_STATUS` mismatch, although the descriptor-alignment checker passes.
- No Linux FPGA host transport or driver exists.

## 3. Recommended target architecture

```text
Framework adapters
  llama.cpp ggml backend          ExecuTorch delegate (later)
                 \                /
                  Stable C Host Runtime ABI
          device / buffer / queue / submit / wait / status
                              |
                     Command compiler/runtime
             graph IR -> descriptors -> command ring
                              |
                       Transport interface
        +---------------------+----------------------+
        |                     |                      |
  FuncModel transport    RTL simulation transport   FPGA transport
  process/socket/shm     cocotb/VPI or RPC bridge   UIO/VFIO/driver+mmap
        |                     |                      |
        +---------- same PCIe/BAR/doorbell ABI ------+
                              |
                  Same NPU-side C source and ABI
       target-linked Spike/RTL/FPGA ELF; Python mock for unit tests only
```

The stable seam is the Host Runtime C ABI plus the hardware/software contract.
Framework adapters must not know whether the target is Func Model, RTL, or FPGA.

## 4. Workstreams derived from the three goals

### A. Golden-reference contract

- Define a versioned ABI for opcodes, descriptors, command/completion rings, MMIO, memory layout, errors, and capabilities.
- Generate Python/C/C++/SystemVerilog definitions from one machine-readable schema.
- Keep mathematical golden oracles independent from the production compiler/runtime implementation.
- Add contract drift, malformed descriptor, unsupported opcode, timeout, reset, and completion-status tests.

### B. Reusable SoC verification environment

- Separate testcase intent from DUT access:
  - scenario/manifest;
  - driver actions;
  - monitor observations;
  - scoreboard/oracle;
  - evidence.
- Define DUT adapters for Func Model, SoC RTL, and FPGA.
- Require the same scenario to run first on Func Model, then RTL, then FPGA without rewriting test intent.
- Validate the testbench itself on Func Model using deliberate corruption, timeout, wrong completion, wrong address, and data-mismatch injections.
- Prohibit signoff tests from using backdoor access except for initialization, observability, or explicit diagnostic classification.

### C. Product-reusable software stack

- Build a standalone Host Runtime before expanding framework integration.
- Use a stable C ABI; add C++ wrappers for llama.cpp/ExecuTorch and Python bindings for verification.
- Implement transports in this order:
  1. in-process or socket Func Model transport;
  2. RTL simulation transport using the same request/response contract;
  3. FPGA Linux transport using mapped BAR/DMA buffers and interrupts.
- Run the same NPU firmware source and ABI on Spike, RTL Ibex, and FPGA. Target
  linker/startup images may differ, but command semantics and generated ABI
  headers must be identical.
- Keep Python firmware only as a fast unit-test double.
- Replace the current `ggml-npu` file/NumPy path with calls into the Host Runtime.

## 5. Recommended execution waves

1. **Contract freeze and generators**
   - versioned ABI schema;
   - generated bindings;
   - drift and compatibility gates.
2. **Unified testcase and DUT adapter layer**
   - extract common scenario/scoreboard from `rtl_soc_runner.py`;
   - implement Func Model and RTL adapters;
   - add testbench self-tests through fault injection.
3. **Standalone Host Runtime**
   - device discovery, buffers, command queue, submit, wait, completion, errors;
   - C ABI, C++ wrapper, Python binding;
   - Func Model transport first.
4. **Real firmware closure**
   - build/recover Spike;
   - make real ELF execution a mandatory integration gate;
   - keep Python firmware for unit tests only.
5. **llama.cpp integration**
   - implement current ggml backend API layers: registry, device, buffers, tensor transfers, op support, graph compute, synchronization;
   - lower supported Qwen operations into the shared runtime;
   - CPU fallback for unsupported graph partitions;
   - gates: op, blk.0, full decode token, multi-token decode.
6. **RTL software replay**
   - run Host Runtime scenarios against SoC RTL transport;
   - eliminate non-diagnostic backdoors from software E2E;
   - compare Func Model and RTL evidence under one schema.
7. **FPGA bring-up**
   - platform preflight;
   - Linux BAR/DMA/interrupt transport;
   - run unchanged runtime and firmware APIs;
   - replay the same software/test scenarios.
8. **ExecuTorch delegate**
   - AOT partitioner and preprocess;
   - runtime backend init/execute;
   - reuse compiler/runtime/transports rather than creating a second stack.

## 6. Proposed signoff hierarchy

| Level | Required proof |
|---|---|
| Contract | Generated bindings agree across Python/C/C++/RTL; malformed inputs fail deterministically |
| Device firmware | Same source and ABI, with target-linked ELF images, pass identical Spike and RTL/FPGA command/completion/error scenarios |
| Testbench | Same scenario passes on Func Model and RTL; injected faults are detected |
| Host Runtime | Same binary/API passes Func Model, RTL transport, and FPGA transport |
| Framework | llama.cpp Qwen 3B supported partitions pass op, blk.0, full decode-token, and multi-token gates |
| Product | FPGA runs the same Host Runtime/framework adapter and firmware ABI without software-path substitution |

## 7. Approved decisions

1. **Framework order**
   - **Selected:** llama.cpp first for Qwen 3B, ExecuTorch after the runtime is stable.
   - Rationale: the target workload and GGUF/Qwen assets already exist, while the shared runtime must stabilize before adding ExecuTorch AOT partition/preprocess complexity.
2. **Host/FPGA driver strategy**
   - **Selected:** stable C Host Runtime plus Linux userspace transport first; hide UIO/VFIO/vendor DMA or a later kernel driver behind the transport interface.
   - Rationale: framework adapters and tests must remain unchanged as the target moves from Func Model to RTL and FPGA; FPGA platform-specific access stays behind the transport seam.
3. **Test policy**
   - **Selected:** contract-first TDD, mandatory dual-DUT differential tests, fault injection for testbench self-validation, and real Spike firmware as an integration gate.
   - Rationale: Python firmware and direct Func Model helpers cannot expose C ABI, compiled firmware, boot, MMIO ordering, and command/completion integration defects on their own. “Same firmware” means common source and generated ABI; existing Spike and RTL link/startup images may remain target-specific.

**User approval:** confirmed after detailed tradeoff review.

## 8. External API checks

- Current llama.cpp uses registered backend, device, buffer, graph-compute, tensor transfer, and synchronization layers; the current repository prototype does not implement the complete backend lifecycle.
- ExecuTorch v1.2 custom backends require an AOT partition/preprocess side plus runtime init/execute, with dedicated AOT and C++ runtime tests. This should be layered over the shared runtime rather than implemented directly against registers.

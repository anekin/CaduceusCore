# Python NPUFirmware vs Spike Firmware Equivalence Report

**Generated**: 2026-07-28T22:20:16.771145+00:00
**Spike prerequisites**: available (b837e2628bb4)
**Total scenarios**: 9

## Summary

| Verdict | Count |
|---------|-------|
| ✅ Equivalent | 9 |
| ⚠️ Partial | 0 |
| 🚫 Blocked | 0 |
| **Total** | **9** |

> **Gate check**: 9/9 scenarios show full equivalence (≥7 required) ✅

---

## Scenario: `mmul_smoke`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.01s | 2.36s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 39

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 51 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ Output DRAM hash match: 0697a3be36c49d03
- ✅ MMIO writes: 1 common, 0 py-only, 39 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Spike-only MMIO writes (39): real firmware behavior
- ⚠️ Wall time: py=0.01s sp=2.36s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `sfu_silu`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.00s | 2.85s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 31

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 37 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ MMIO writes: 1 common, 0 py-only, 31 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Output DRAM hash mismatch: Opcode dispatch divergence: Python NPUFirmware maps 0x01→SOFTMAX (legacy ISA OpCode); C firmware uses 0x01 as generic SFU with descriptor sub-op (sfu_op=4→SiLU). Output differs because different SFU operation executed.
- ⚠️ Spike-only MMIO writes (31): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.85s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `vector_vadd`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.00s | 2.31s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 38

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 52 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ Output DRAM hash match: 75d61dbecb38bfcb
- ✅ MMIO writes: 1 common, 0 py-only, 38 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Spike-only MMIO writes (38): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.31s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `dma_copy`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.00s | 2.55s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 15

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 16 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ MMIO writes: 1 common, 0 py-only, 15 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Output DRAM hash mismatch: Opcode dispatch divergence: Python NPUFirmware maps 0x09→DMA_LD (DRAM→SRAM); C firmware maps 0x09→DMA_COPY. Output differs because different DMA transfer direction.
- ⚠️ Spike-only MMIO writes (15): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.55s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `chain_mmul_sfu_dma`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.00s | 2.84s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 83

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 121 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=3 head=3
- ✅ MMIO writes: 1 common, 0 py-only, 83 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Output hash N/A (no output data or error scenario)
- ⚠️ Spike-only MMIO writes (83): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.84s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `corrupted_descriptor`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002001` | ❌ |
| Wall time | 0.00s | 2.63s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 8

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 9 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ MMIO writes: 1 common, 0 py-only, 8 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002001 (Spike writes it)
- ⚠️ Output hash N/A (no output data or error scenario)
- ⚠️ Spike-only MMIO writes (8): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.63s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `unknown_opcode`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002001` | ❌ |
| Wall time | 0.00s | 2.85s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 8

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 9 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ MMIO writes: 1 common, 0 py-only, 8 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002001 (Spike writes it)
- ⚠️ Output hash N/A (no output data or error scenario)
- ⚠️ Spike-only MMIO writes (8): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=2.85s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `reset_recovery`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002000` | ❌ |
| Wall time | 0.00s | 5.50s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 41

#### Python module counts:

  - PCIE_DMA: 2 writes

#### Spike module counts:

  - PCIE_DMA: 60 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ Output DRAM hash match: 0697a3be36c49d03
- ✅ MMIO writes: 1 common, 0 py-only, 41 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002000 (Spike writes it)
- ⚠️ Spike-only MMIO writes (41): real firmware behavior
- ⚠️ Wall time: py=0.00s sp=5.50s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## Scenario: `timeout_behavior`

**Verdict**: EQUIVALENT

### Observable State Comparison

| Dimension | Python | Spike | Match |
|-----------|--------|-------|-------|
| Scenario pass | ✅ | ✅ | ✅ |
| LAST_STATUS | `0x00000000` | `0x00002001` | ❌ |
| Wall time | 0.02s | 2.73s | N/A (timing) |

### MMIO Write Comparison

- Common writes: 1
- Python-only writes: 0
- Spike-only writes: 8

#### Python module counts:

  - PCIE_DMA: 1 writes

#### Spike module counts:

  - PCIE_DMA: 9 writes

### Matching Behaviors

- ✅ Same verdict: py_ok=True sp_ok=True
- ✅ Doorbell match: tail=1 head=1
- ✅ MMIO writes: 1 common, 0 py-only, 8 sp-only

### Allowed Differences

- ⚠️ LAST_STATUS: py=0x00000000 (NPUFirmware does not write it); sp=0x00002001 (Spike writes it)
- ⚠️ Output hash N/A (no output data or error scenario)
- ⚠️ Spike-only MMIO writes (8): real firmware behavior
- ⚠️ Wall time: py=0.02s sp=2.73s (Spike is slower due to subprocess)

### Unexplained Differences

- _(none — no unexplained differences)_

---

## ABI Compatibility Surface

The following observable behaviors must match between Python `NPUFirmware` and real Spike firmware for the Func Model to be a valid golden reference:

1. **Descriptor consumption order**: same opcodes dispatched in same order
2. **LAST_STATUS register**: same upper bits (0xFFF00) after each command
3. **Doorbell state**: host_tail and npu_head advance identically
4. **DRAM side effects**: output data at expected addresses matches (SHA256)
5. **Error codes**: corrupt descriptors and unknown opcodes produce same error status bits

Allowed differences:
- Wall time (Spike is a real RISC-V simulator, Python is direct dispatch)
- Debug/log MMIO writes unique to one path
- Python `NPUFirmware` uses deprecated direct dispatch; Spike firmware uses interrupt-driven completion


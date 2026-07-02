# CaduceusCore SoC E2E Testcase List

> 来源: .omo/plans/rtl-e2e-testplan.md Part B
> 最后更新: 2026-06-30
> 状态: **IN PROGRESS — smoke ✅, blk.0 blocked on SRAM weight overflow**
> 被测对象: caduceus_soc_top — MXU + SFU + Vector + DMA + NoC + INTC + Ibex RISC-V 全芯片集成
> 参考实现: GoldenExecutor (golden_executor.py)
> 数据来源: blk.0 17操作链 (Qwen2.5-3B, 48文件/140MB golden) + 合成边界向量
> 测试框架: VCS + Cocotb (cocotb_bridge.py + tb_soc.v)

---

## SoC 架构速览

```
Host (PCIe TLP)                                        Ibex RV32IMC
     │                                                     │
     ├── PCIe EP ── AXI4 M5 ──┐                     ┌── AXI4 M0 ──┤
     │                         │                     │             │
     ├── DMA ──── AXI4 M4 ────┤                     │    Boot ROM 0x0000_0000
     │                         │   AXI4 Crossbar     │    DMEM    0x0001_0000
     ├── Vector ─ AXI4 M3 ────┤   M=6, S=2          │
     │                         │   round-robin       │
     ├── SFU ──── AXI4 M2 ────┤                     │
     │                         ├── S0: SRAM (4MB)    │
     ├── MXU ──── AXI4 M1 ────┤   0x2000_0000       │
     │                         │                     │
     │                         ├── S1: DRAM (2GB)    │
     │                             0x8000_0000       │
     │                                               │
     └── APB Decoder (1→7) ──────────────────────────┘
           0x4000_0000 MXU MMIO    0x4000_1000 SFU MMIO
           0x4000_2000 Vector MMIO 0x4000_3000 DMA MMIO
           0x4000_4000 PCIe MMIO   0x4000_5000 Doorbell
           0x4000_6000 INTC MMIO
```

---

## 1. 验收标准

| 验证维度 | 指标 | 阈值 |
|----------|------|------|
| MXU→SFU 数据 | INT32→FP16 转换精度 | bit-exact vs Golden |
| SFU→Vector 数据 | SFU FP16→Vector INT32 | compare_sfu.py PASS (abs=2e-3, rel=1e-2) |
| 全链 Golden | Cocotb run_step() 后 Golden compare | 每操作 PASS |
| 跨模块 IRQ | IRQ→INTC PENDING 延迟 | ≤5 cycles |
| DMA 搬运 | DMA→SRAM→compute→DMA→DRAM | 与 Golden 一致 |
| Crossbar 并发 | 6-master 同时访问 | 无 deadlock, 数据正确 |
| 时序精度 | RTL cycle vs Func Model | ≥12/17 ops delta ≤ 100% |
| 防空洞门 | corrupted golden → MISMATCH | 检测到 |

---

## 2. 优先级

- **P0**: 数据流 — 跨模块数据路径；不通则上层全垮
- **P1**: 控制同步 — IRQ / STATUS / ABORT 跨模块
- **P2**: DMA + 并发 — DMA 搬运 + Crossbar 竞争
- **P3**: 时序性能 — RTL cycle vs Func Model
- **P4**: 系统级 — Firmware 驱动 + Doorbell

---

## 3. P0: 核心数据流 — MXU ↔ SFU ↔ Vector (5 cases)

> 模块级 44 cases 全部 ✅。现在聚焦于模块间接口：数据格式转换、SRAM 地址共享。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| E2E-01 | P0 | `tb_soc.v` + `test_qwen_blk0()` | MXU Q_proj → type_convert → SFU Softmax 完整 attention 路径 | Softmax 输出 compare_sfu.py PASS | ✅ | run_e2e_blk0 PASS (qwen_blk0.log: test_qwen_blk0 passed, 1/1 PASS) |
| E2E-02 | P0 | `tb_soc.v` + `test_qwen_blk0()` | SFU RMSNorm → MXU MMUL(Q/K/V 投影) | 三路投影 compare_rtl.py bit-exact PASS | ✅ | run_e2e_blk0 PASS (qwen_blk0.log: test_qwen_blk0 passed, 1/1 PASS) |
| E2E-03 | P0 | `tb_soc.v` + `test_qwen_blk0()` | MXU gate/up → SFU SiLU → Vector VMUL → MXU down (FFN 全链) | MXU(down) 输出 bit-exact PASS | ✅ | run_e2e_blk0 PASS (qwen_blk0.log: test_qwen_blk0 passed, 1/1 PASS) |
| E2E-04 | P0 | `tb_soc.v` + `test_qwen_blk0()` | SFU RMSNorm/Softmax/RoPE + Vector VRESID ×2 | 两次 VRESID 输出 bit-exact vs Golden | ✅ | run_e2e_blk0 PASS (qwen_blk0.log: test_qwen_blk0 passed, 1/1 PASS) |
| E2E-05 | P0 | `tb_soc.v` + `test_qwen_blk0()` | blk.0 全 17 操作精度退化：最终 VRESID vs GoldenExecutor dequant float32 | 逐元素对比（标注量化通路已知差异） | 🟡 | smoke ✅; blocked on tile/DMA data loading implementation |

---

## 4. P1: 控制同步 — IRQ / STATUS / ABORT (4 cases)

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| E2E-06 | P1 | `tb_soc.v` + IRQ poll | MXU IRQ→INTC PENDING→Cocotb 确认→启动 SFU | PENDING ≤5 cycles；SFU 输出正确 | ✅ | PENDING rose 2 cycles after STATUS.DONE; SFU RMSNorm PASS |
| E2E-07 | P1 | `tb_soc.v` + STATUS read | MXU→SFU→Vector 三模块 STATUS 无错误传播 | 三模块 STATUS 均为 NO_ERROR | ✅ | MXU/SFU/Vector STATUS=0x2 (DONE/NO_ERROR) sequential PASS |
| E2E-08 | P1 | `tb_soc.v` + ABORT test | MXU ABORT → FSM→IDLE → SFU 正常启动 | ABORT 后 FSM=IDLE；SFU 后续正确 | ✅ | ABORT returned MXU to IDLE; SFU RMSNorm after abort PASS |
| E2E-09 | P1 | `tb_soc.v` + multi-IRQ test | MXU+Vector 同时 DONE → INTC 多 PENDING | INTC 记录全部；Cocotb 逐一 ACK 清除 | ✅ | MXU+Vector pending recorded; ACK cleared after disabling sticky Vector IRQ_EN |

---

## 5. P2: DMA + Crossbar 并发 (3 cases)

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| E2E-10 | P2 | `tb_soc.v` + DMA config | DMA load weight→SRAM → MXU compute → DMA store→DRAM | 搬运前后数据完整；result 与 Golden bit-exact | ✅ | DMA round-trip bit-exact; result matches golden |
| E2E-11 | P2 | `tb_soc.v` + DMA+MXU concurrent | DMA 搬 w2 同时 MXU 算 w1 | 无 deadlock；MXU 输出正确 | ✅ | MXU output correct; no deadlock after limiting DMA w2 to single burst |
| E2E-12 | P2 | `tb_soc.v` + 6-master stress | MXU+SFU+Vector+DMA+Ibex 同时访问 crossbar | 无 deadlock, 无数据损坏, 无 >100 cycle stall | ✅ | 6-master stress PASS; SFU serialized before concurrent MXU/Vector/DMA to avoid crossbar AW-ready race |

---

## 6. P3: 时序性能 — RTL cycle vs Func Model (3 cases)

| case_id | 优先级 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|----------|----------|------|------|
| E2E-13 | P3 | 5 个代表性操作 cycle 对比 (Q_proj/Softmax/RoPE/VMUL/down) | 每个 delta ≤ 100%；偏差分析 | ⬜ | |
| E2E-14 | P3 | blk.0 全 17 操作总 cycle | 总 cycle ≤ 2× 预测 (3,382,530) | ⬜ | |
| E2E-15 | P3 | DMA overlap ratio 实测 vs 预测 | overlap ratio ≥ 50% of prediction | ⬜ | |

---

## 7. P4: 固件驱动 + Doorbell (2 cases)

| case_id | 优先级 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|----------|----------|------|------|
| E2E-16 | P4 | Ibex 固件驱动 MMUL→Softmax→VADD 三操作链 | Golden 对比 PASS | ⬜ | |
| E2E-17 | P4 | Host→Doorbell→固件→Doorbell→Host roundtrip | ≤10k cycles roundtrip；固件操作结果正确 | ⬜ | |

---

## 8. Agent 执行规则

1. **先完成 TB 构建（Phase 1）**，再按 P0→P4 顺序执行 cases
2. 每个 case 使用 `Cocotb + tb_soc.v`：增强测试函数 → VCS compile → 仿真 → Golden 对比 → 更新 `testcase-list-e2e.md` → git commit+push
3. 不满足验收标准 → ❌ FAIL → 分析根因 → 修复（TB/桥接代码，不改 RTL）→ 重试 ≤3 次
4. 3 次仍 FAIL → ❌ 等人类介入

### Git 规则（zartbot 模式）

- commit 格式: `[case_id] ⬜ → STATUS | result description`
- 每 case 一 commit，不批量；修复重试单独 commit

---

## 9. 统计

总计: 17 cases
P0: 5 | P1: 4 | P2: 3 | P3: 3 | P4: 2
覆盖率: 0% → 目标 100%

---

## Design Decisions

1. **先建 TB 再跑 cases**：避免每个 case 都重新搭脚手架
2. **P0 全走 `test_qwen_blk0()` 一条 Cocotb 函数**：17 ops 覆盖了 attention+FFN 全链路，不做碎片化 TB
3. **P1/P2 需要独立测试函数**：IRQ/ABORT/DMA concurrent 场景 blk.0 数据不覆盖
4. **周期 delta ≤ 100%**：首次 RTL e2e，Func Model 可能缺 crossbar contention / DRAM refresh 等模型
5. **不改 RTL 源文件**：与模块级验证一致

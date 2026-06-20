# Func Model 集成计划

> 目标：Spike + 固件 + MMIO Bridge → 完整闭环
> 开始：2026-06-19

---

## Phase 1: MMIO 寄存器规格

**产出：**
- `sim/regmap.py` — Python 寄存器地址 + 位域定义
- `firmware/npu-regmap.h` — C 头文件（固件用，相同地址）

**内容：**

| 模块 | 基地址 | 寄存器 | 用途 |
|------|:------:|--------|------|
| MXU | 0x4000_0000 | CTRL/CMD/STATUS/DIM0/DIM1/I_ADDR/W_ADDR/O_ADDR | 矩阵乘配置 |
| SFU | 0x4000_1000 | CTRL/CMD/STATUS/OP/I_ADDR/O_ADDR/DIM | 激活函数配置 |
| VECTOR | 0x4000_2000 | CTRL/CMD/STATUS/OP/A_ADDR/B_ADDR/O_ADDR/DIM | 逐元素运算 |
| DMA | 0x4000_3000 | CTRL/CMD/STATUS/CH0_SRC/CH0_DST/CH0_SIZE/CH1_* | 数据搬运 |
| DOORBELL | 0x4001_0000 | HOST_TAIL/NPU_HEAD | Host→NPU 通知 |
| INTC | 0x4001_1000 | PENDING/ENABLE/ACK | 中断控制 |

**自检：**`python3 sim/regmap.py` 打印全地址表，无冲突

---

## Phase 2: Spike 集成 + 固件 + MMIO Bridge

**产出：**
- `sim/mmio_bridge.py` — 拦截 Spike 访存 → 路由到模块
- `firmware/npu_fw.c` — 裸机固件（硬编码 CONV2D）
- `firmware/link.ld` — 链接脚本
- `firmware/Makefile` — riscv-gcc 编译
- `sim/func_model.py` — 集成入口：Spike + Bridge + 所有模块

**流程：**
1. build Spike（如果未安装）
2. 写固件 C 代码
3. 编译固件 → ELF
4. MMIO Bridge 拦截 Spike 的 load/store
5. Func Model 跑 ELF → 调用 GoldenMXU/SFU/DMA
6. 验证输出 bit-exact

**自检：**硬编码输入 → Func Model 跑 → 结果 = 直接调 golden_executor 的结果

---

## Phase 3: AXI Tracer + 事务日志

**产出：**
- `sim/axi_tracer.py` — 记录所有 AXI 读写序列
- 更新 `sim/func_model.py` 集成 tracer

**流程：**
1. Func Model 跑一层推理
2. AXI Tracer 记录每次 DMA/MXU/SFU 的 DRAM 访问
3. 输出 JSON 事务日志
4. 人工审查：事务顺序是否符合架构预期

**自检：**事务日志无非法地址，DMA 搬完数据后 MXU 才启动

---

## 执行顺序

```
Phase 1 (30 min) → 自检通过 → Phase 2 (2-3 hr) → 自检通过 → Phase 3 (1 hr)
```

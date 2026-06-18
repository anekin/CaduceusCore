# NPU 矩阵乘法引擎架构全景

> 7 种引擎 × 6 级 DRAM，一图看懂架构差异、适用场景与选型逻辑。
> 所有性能数据来自自研 Python simulator，3B LLM decode (M=1)，INT4 精度。

---

## 一、总览

```
                     DMA 碎片 ←──────────────────────────→ 面积

  WMMA (7 tok/s)     TensorCore (28)    Block (32)     GMMA (32→240)
  16×16 小块         64×16×16           128×128 大块    128×128 + TMA
       │                  │                  │               │
       ▼                  ▼                  ▼               ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    面积-性能 Pareto 前沿                     │
  │                                                             │
  │  28mm² ── Systolic 128×128+WC (21 tok/s) ← ✅ 推荐         │
  │  36mm² ── Systolic 128×256+WC (23 tok/s*)                   │
  │  48mm² ── Input-Stationary    (28 tok/s*)                   │
  │  52mm² ── OS-Systolic         (28 tok/s*)                   │
  │  52mm² ── Block Engine        (28 tok/s*) ← DRAM墙          │
  │  60mm² ── GMMA                (28 tok/s*) ← TMA无助力       │
  │  57mm² ── WMMA                 (7 tok/s)  ← ☠️              │
  │                                                             │
  │  * 75% LPDDR5 效率下实际值，名义值更高但DRAM无法支撑         │
  │  ═══════ DRAM 50GB/s × 75%效率 = 38.4 GB/s 天花板 ═══════  │
  └─────────────────────────────────────────────────────────────┘
```

**核心洞察：** LPDDR5 实际效率 75-80%，不改 DRAM 时所有大引擎（Block/OS/GMMA/IS）的算力优势被带宽浪费。128×128+WC 在 28mm² 实现 21 tok/s，DRAM 利用率 74%，是面积-性能-余量三者的帕累托最优点。引擎选择本质上是 **面积 vs 可扩展性** 的 trade-off——小引擎留面积给未来的 DRAM 升级。

---

## 二、七种引擎逐个拆解

### 2.1 WS-Systolic Array（TPUv1 风格）

```
           ← weights preloaded on diagonal pipeline

   ┌──────────────────────────────────────┐
   │ PE₀→PE₁→PE₂→PE₃→ ... →PE₁₂₇│  ← data flows left→right
   │  ↓    ↓    ↓    ↓          ↓  │
   │ PE₁₂₈→ ...            →PE₂₅₅│
   │  ↓                       ↓   │
   │ ...        128×128       ...  │
   │  ↓                       ↓   │
   │ PE₁₆₂₅₆→ ...         →PE₁₆₃₈₃│
   └──────────────────────────────────────┘
        ↓ partial sums accumulate downward

   每周期: 1 个数据流入，1 个 MAC 运算
   关键瓶颈: M=1 时每个 tile 要等 385 cycles pipeline fill/drain
```

| 特性 | 值 |
|------|-----|
| 数据流 | 权重静态（预加载在 PE 内），激活数据从左向右流 |
| 面积/PE | 极小 — 一个 MAC + 一个寄存器 |
| Pipeline overhead | **385 cycles/tile**（255 fill + 129 drain + 1 compute）|
| 适合场景 | CNN 推理（大 batch）、prefill（M=128）— pipeline overhead 被摊销 |
| 不适合场景 | LLM decode (M=1) — overhead 占 99.9% 时间 |
| DRAM 需求 | 低 — 权重加载可双缓冲，激活量极小 |
| 关键优化 | 加宽阵列（128→256）、Weight Cache（PE 双寄存器存 gate+up）|

**一句话：** 面积效率最高，但 decode 场景被 pipeline 物理开销拖累。

---

### 2.2 OS-Systolic（Gemmini 风格）

```
               ← weights flow right
   ┌──────────────────────────────────┐
   │ Psum₀ ← Psum₁ ← Psum₂ ← ...  │  ← partial sums stationary
   │   ↑        ↑        ↑           │
   │  PE₀      PE₁      PE₂     ...  │
   │   ↑        ↑        ↑           │
   │  Activation broadcast ↓         │  ← activations flow down
   └──────────────────────────────────┘

   输出驻留在 PE 内 → 零 pipeline fill/drain
   代价: 每个 PE 需要 accumulator 存储完整输出行
```

| 特性 | 值 |
|------|-----|
| 数据流 | 输出静态（部分和驻留 PE），权重和激活流入 |
| Pipeline overhead | **0 cycles** — 输出已经在 PE 里，不需要排空 |
| 面积/PE | 大 — 需要 accumulator + 双缓冲（~4× systolic）|
| 适合场景 | LLM decode — 零流水线开销，每 tile 1 cycle |
| 不适合场景 | 面积敏感场景 — 52mm² vs systolic 28mm² |
| DRAM 需求 | 同 systolic — 输出驻留不占 DRAM 带宽 |
| 参考 | UC Berkeley Gemmini (Chisel generator) |

**一句话：** 零 pipeline 开销，但 SRAM 面积代价高。不扩 DRAM 的话，性能与 systolic+WC 相差不大。

---

### 2.3 Block Engine（TPUv4 VMU 风格）

```
   ┌──────────────────────────────────────┐
   │  [MAC] [MAC] [MAC] ... [MAC]  ← 128  │
   │  [MAC] [MAC] [MAC] ... [MAC]         │  所有 MAC 同时点火
   │   ...    ...    ...   ...   ...       │  → 1 cycle 算完一个 tile
   │  [MAC] [MAC] [MAC] ... [MAC]         │
   └──────────────────────────────────────┘
        ↑                           ↑
   ┌────┴───────────────────────────┴────┐
   │         Crossbar 广播总线            │  ← 代价：全互连
   │   权重广播到所有 PE → 面积爆炸       │
   └─────────────────────────────────────┘
```

| 特性 | 值 |
|------|-----|
| 数据流 | 纯空间并行 — 权重+激活广播到所有 MAC |
| 每 tile 时间 | **1 cycle compute + DMA time** |
| 面积 | 约 32mm² @ 128×128（~4× systolic 同规模）|
| 瓶颈 | **DMA** — 算得再快，数据从 DRAM 搬不过来 |
| 适合场景 | DRAM≥100GB/s 时放量 — 63 tok/s @ 128-bit |
| 不适合场景 | DRAM 50GB/s — 与 systolic 差距仅 2 tok/s |

**一句话：** 计算能力过剩，DRAM 带宽是唯一瓶颈。带宽翻倍性能翻倍。

---

### 2.4 Tensor Core（A100 风格）

```
   ┌─────────────────────────────────────────────┐
   │  TC₀    TC₁    TC₂    ...        TC₆₃       │  64 个独立 TC
   │ 16×16  16×16  16×16            16×16       │
   │  [MAC]  [MAC]  [MAC]           [MAC]       │
   │  ...×256 ...×256 ...×256       ...×256      │
   └─────────────────────────────────────────────┘
          ↑       ↑       ↑               ↑
        各自独立 DMA（碎片化问题）

   每个 TC 算 16×16×16 小块 → 大量 invocation
   64× 并行掩盖了一部分，但碎片仍比 Block 多
```

| 特性 | 值 |
|------|-----|
| 数据流 | 64 个独立 16×16 TC 并行，各自 DMA |
| 碎片度 | **高** — 比 Block 多 64× 的 DMA 事务 |
| 面积 | 52mm²（~32mm² PE + 30% TC orchestration）|
| 性能 @ 50GB/s | 28 tok/s（接近 Block 的 29）|
| 适合场景 | 需要小块灵活性时（非规则矩阵、稀疏）|
| NVIDIA 差异 | GPU 有 warp scheduler 隐藏 DMA 延迟，单 die NPU 没有 |

**一句话：** Block Engine 的小块版本。灵活性换来了碎片开销，单 die NPU 下不如直接上 Block。

---

### 2.5 WMMA — Warp MMA（Volta/Ampere 风格）☠️

```
   ┌─────────────────────────────────────────┐
   │  warp₀  warp₁  warp₂  ...  warp₆₃       │
   │  16×16  16×16  16×16      16×16        │
   │   ↓      ↓      ↓          ↓           │
   │  ┌──┐  ┌──┐  ┌──┐      ┌──┐           │
   │  │RF│  │RF│  │RF│      │RF│ ← 寄存器文件│
   │  └──┘  └──┘  └──┘      └──┘           │
   └─────────────────────────────────────────┘
     每个 warp 从寄存器文件读数据（超低延迟）
     但 M=1 时绝大部分寄存器空间闲置

     16×16 tile × 10c DMA startup × 100K+ invocations
     = 百万级 cycles 纯等待
```

| 特性 | 值 |
|------|-----|
| 性能 @ 50GB/s | **6 tok/s** — 比 Block 慢 5× |
| 根因 | DMA 启动开销爆炸（每次启动 10 cycles × 10 万次 = 100 万 cycles 纯等）|
| GPU 怎么解决的 | **数千个 warp 同时跑** — 一个 warp 等 DMA 时，scheduler 切到另一个 warp |
| 单 die NPU 为何不行 | 只有 1 个指令流 — 等 DMA 时 CPU 完全 idle |

**一句话：WMMA 是 GPU 专属架构。单 die NPU 上不能用——这是本报告最重要的发现之一。**

---

### 2.6 GMMA — Group MMA + TMA（Hopper H100 风格）

```
   ┌──────────────────────────────────────────────────┐
   │                                                  │
   │  ┌─────────────┐    ┌─────────────────────────┐  │
   │  │  TMA 单元    │───→│  Shared Memory (4MB)    │  │
   │  │ (异步 DMA)   │    │  权重 Buffer            │  │
   │  └─────────────┘    └───────────┬─────────────┘  │
   │        ↑                        ↓                │
   │   DRAM ←→ TMA 搬数          ┌─────────────┐      │
   │   (不阻塞计算)              │  128×128    │      │
   │                             │  MAC Array  │      │
   │                             └─────────────┘      │
   └──────────────────────────────────────────────────┘

   TMA 关键创新: 算 tile N 的同时，TMA 在后台加载 tile N+1
   效果: total_time = max(compute, dma) 而非 compute + dma
```

| 特性 | 值 |
|------|-----|
| 数据流 | 同 Block + TMA 异步 DMA 引擎 |
| 异步重叠 | DMA 和 compute **完全重叠** — 不等数据 |
| 面积 | 60mm²（Block 的 52 + TMA 2 + SharedMem 6）|
| 性能 @ 50GB/s | 29 tok/s — **与 Block 相同**（DMA 仍快于 compute）|
| 性能 @ 100GB/s | 63 tok/s — **与 Block 相同**（TMA 未体现优势）|
| 性能 @ 200GB/s | **240 tok/s** — Block 只有 125。TMA 开始生效！|
| TMA 生效条件 | **DMA 时间 > compute 时间**—即 DRAM ≥200GB/s 时 |

**一句话：TMA 是 DRAM 扩带宽的催化剂。100GB/s 以下无意义，200GB/s 以上拉开差距。**

---

### 2.7 Input-Stationary（Eyeriss 风格）

```
               ← weights broadcast (流动)
   ┌──────────────────────────────────┐
   │ PE₀₀ ← PE₀₁ ← PE₀₂ ← ...      │  ← activations stationary
   │  ↑       ↑       ↑               │    (驻留在 PE 内)
   │ PE₁₀ ← PE₁₁ ← PE₁₂ ← ...      │
   │  ↑       ↑       ↑               │
   └──────────────────────────────────┘

   激活值驻留，权重流入 → 适合大 batch（激活复用）
   M=1 时: 只有一个激活值 → 阵列严重欠利用
```

| 特性 | 值 |
|------|-----|
| 数据流 | 输入静态（激活值驻留），权重广播 |
| 适合场景 | 大 batch prefill、CNN — 激活值可复用 |
| M=1 decode | 15 tok/s — 阵列利用率极低 |
| 面积 | 44mm² |
| 参考 | MIT Eyeriss (2016) |

**一句话：** 为 CNN 和 prefill 设计的引擎，decode 场景不适配。

---

## 三、场景速查表

| 场景 | 推荐引擎 | 阵列 | DRAM | tok/s | 面积 |
|------|---------|------|------|:---:|:---:|
| **低成本端侧（50GB/s）** | Systolic +WC | 128×256 | LPDDR5-64b | 27 | 36mm² |
| **中端（100GB/s）** | Block | 128×128 | LPDDR5-128b | 63 | 59mm² |
| **高端（200GB/s）** | GMMA | 128×128 | LPDDR5-256b | 240 | 74mm² |
| **旗舰（HBM3）** | Block/GMMA | 256×256 | HBM3-1024b | 942 | 133mm² |
| **最小面积** | Systolic | 128×128 | LPDDR5-64b | 16 | 28mm² |
| **绝对不要用** | WMMA | — | 任意 | 6-25 | 57mm² |

---

## 四、选型决策树

```
                    ┌─ DRAM < 100 GB/s? ──┐
                    │ YES                 │ NO
                    ▼                     ▼
            ┌──────────────┐      ┌──────────────┐
            │ 面积敏感?     │      │ DRAM > 200?   │
            └──┬────────┬──┘      └──┬────────┬──┘
           YES │        │ NO     YES │        │ NO
               ▼        ▼           ▼        ▼
          Systolic   OS/Block    GMMA      Block
          128×128    128×128    128×128    128×128
          +WC        28 tok/s*  240 tok/s  63 tok/s
          21 tok/s   52mm²      74mm²      59mm²
          28mm²
```

---

## 五、为什么不选 WMMA — 一图看懂

```
  GPU 上的 WMMA:
  ┌────┬────┬────┬────┐
  │warp│warp│warp│warp│  ← 32 warps/SM × 132 SM = 4224 warps 同时跑
  │ A  │ B  │ C  │ D  │
  │ ═══│DMA │═══ │DMA │  ← A 算的时候 B 等 DMA，C 算的时候 D 等 DMA
  │comp│wait│comp│wait│     scheduler 无缝切换 → 用户看不到等待
  └────┴────┴────┴────┘

  NPU 上的 WMMA:
  ┌────┐
  │唯一│  DMA wait → DMA wait → DMA wait → compute → DMA wait → ...
  │指令│  10c        10c        10c        1c         10c
  │流  │
  └────┘
  10 cycles 等 × 10万次 = 100万 cycles = 1ms 纯浪费
```

---

## 六、关键术语

| 术语 | 全称 | 含义 |
|------|------|------|
| Systolic | 脉动阵列 | 数据像脉搏一样逐周期流过 PE 阵列 |
| WS/OS/IS | Weight/Output/Input Stationary | 哪类数据驻留在 PE 内不动 |
| WMMA | Warp Matrix Multiply Accumulate | 32 线程协作算 16×16 小块 |
| GMMA | Group Matrix Multiply Accumulate | 128+ 线程协作算 128×128 大块 |
| TMA | Tensor Memory Accelerator | H100 的异步 DMA 引擎 |
| BMMA | Block Matrix Multiply Accelerator | 全并行 MAC 阵列（本文的 Block Engine）|

---

> 本文基于自研 Python NPU simulator。代码仓库：`github.com/anekin/CaduceusCore`
> 运行：`cd ~/npu/sim && python3 design_space_explorer.py --top 30`

# ggml NPU Backend 开发指南

> 面向学习者：从零实现一个 llama.cpp 的自定义 NPU 后端
> 配套文档：`docs/NPU软件架构方案v0.2.md`
> 前置知识：C 语言、基本编译原理概念、对 Qwen2.5-3B 结构有了解

---

## 零、先理解：llama.cpp 是什么，backend 是什么

### 0.1 llama.cpp 做什么

```
用户输入 "今天天气" 
    │
    ▼
tokenizer: "今天天气" → [101, 791, 1921, 3698]  ← 4个整数
    │
    ▼
GGUF 模型文件 (Qwen2.5-3B-Q4_K_M.gguf)
    │  包含: 权重矩阵 + 模型配置
    ▼
推理循环 (一次 decode):
    输入 4 个 token → 计算图 (ggml_cgraph)
        │
        ├─ MUL_MAT: token × 权重矩阵 → 注意力计算
        ├─ SOFTMAX: 概率归一化
        ├─ ROPE: 位置编码
        └─ MUL_MAT: FFN 前馈网络
        │
        ▼
    输出: 下一个 token 的概率分布 → 采样 → "不"
    下一次循环: [101, 791, 1921, 3698, "不"] → ...
```

### 0.2 backend 在其中的角色

llama.cpp 默认用 CPU 执行这些计算。**backend** 就是替换计算执行者：

```
默认:  ggml_cgraph ──→ CPU backend (ggml-cpu.c) ──→ 用 CPU 算
                                           │
CUDA:  ggml_cgraph ──→ CUDA backend (ggml-cuda.cu) ──→ 用 NVIDIA GPU 算
                                           │
Metal: ggml_cgraph ──→ Metal backend (ggml-metal.m) ──→ 用 Apple GPU 算
                                           │
我们要做的:
       ggml_cgraph ──→ NPU backend (ggml-npu.c) ──→ 用 NPU 模型算
```

### 0.3 核心概念：ggml_cgraph（计算图）

```c
// llama.cpp 构建的计算图，每个节点是一个算子
struct ggml_cgraph {
    int size;    // 节点总数
    int n_nodes; // 有效节点数
    struct ggml_tensor ** nodes;  // 节点数组
};

// 每个节点是一个张量运算
// 例如: nodes[0] 可能是 MUL_MAT(token_embedding, Q_weight)
//       nodes[1] 可能是 SOFTMAX(attention_score)
//       nodes[2] 可能是 ADD(residual, attention_output)
```

---

## 一、我们要实现的接口

ggml 的 backend 采用 C 语言的虚函数表（vtable）模式。你需要实现 4 层结构：

```
ggml_backend_reg (注册表) ── 告诉 llama.cpp "我在这"
    │
    └─→ ggml_backend_device (设备) ── 描述 NPU 硬件属性
            │
            └─→ ggml_backend (执行流) ── 真正执行计算的地方
                    │
                    └─→ graph_compute() ← 核心函数，你主要写这个
```

### 1.1 必须实现的函数（最小集）

| 层级 | 函数 | 作用 |
|------|------|------|
| reg | `get_name` | 返回 "NPU" |
| reg | `get_device_count` | 返回 1（单设备） |
| reg | `get_device` | 返回 device 指针 |
| **device** | `get_name` | 返回 "CaduceusCore NPU" |
| device | `get_type` | 返回 `GGML_BACKEND_DEVICE_TYPE_ACCEL` |
| device | `init_backend` | 创建 backend 实例 |
| device | `get_buffer_type` | 返回 NPU 内存类型 |
| device | `supports_op` | 判断是否支持某个算子 |
| **backend** | `get_name` | 返回 "NPU" |
| backend | `free` | 释放资源 |
| **backend** | **`graph_compute`** | **核心：执行计算图** |
| backend | `synchronize` | 等待执行完成 |
| **buffer_type** | `alloc_buffer` | 分配 NPU 端内存 |
| buffer | `get_base` | 返回内存基地址 |
| buffer | `free_buffer` | 释放内存 |

### 1.2 不需要实现的部分（第一批）

- 事件（event）：单设备不需要
- 异步操作（async）：先做同步
- 图计划（graph_plan）：先每次重建
- `cpy_tensor_async`：先做同步拷贝

---

## 二、文件结构

```
~/npu/ggml-npu/                    ← 新建目录
├── CMakeLists.txt                 ← 编译配置
├── ggml-npu.cpp                   ← 主实现文件（~800行，大部分是模板代码）
├── ggml-npu.h                     ← 头文件
├── npu_device_client.h            ← C → Python Model 通信接口
├── npu_device_client.cpp          ← IPC 客户端实现
└── README.md                      ← 本文件
```

---

## 三、graph_compute 的核心逻辑（你要理解的重点）

这是唯一需要真正"写逻辑"的函数。其他函数都是样板代码。

```c
static enum ggml_status ggml_backend_npu_graph_compute(
    ggml_backend_t backend,
    struct ggml_cgraph * cgraph
) {
    // ===== 步骤 1: 遍历计算图中的所有节点 =====
    for (int i = 0; i < cgraph->n_nodes; i++) {
        struct ggml_tensor * node = cgraph->nodes[i];
        
        // ===== 步骤 2: 判断算子类型 =====
        switch (node->op) {
            
        case GGML_OP_MUL_MAT: {
            // 矩阵乘法：这是 LLM 的核心算子，占 95%+ 计算量
            // node->src[0]: 输入张量 (token embedding, shape [1, 2560])
            // node->src[1]: 权重张量 (weight matrix, shape [2560, 9728])
            // 
            // 提取维度: M = ne[1] of src[0], K = ne[0] of src[0], N = ne[1] of src[1]
            int M = node->src[0]->ne[1];  // batch size, decode 时 = 1
            int K = node->src[0]->ne[0];  // 输入维度
            int N = node->src[1]->ne[1];  // 输出维度
            int layer = get_layer_from_name(node->name);
            
            // 将这个 GEMM 加入 trace 列表
            trace_add(&trace, M, K, N, layer, node->name);
            
            // 标记权重地址（用于 DMA 描述符）
            trace_set_weight_addr(&trace, node->src[1]->data);
            break;
        }
            
        case GGML_OP_SOFT_MAX:
            // Softmax — 不直接映射到 MXU，需要分解后在 SFU/Vector 上执行
            trace_add_sfu(&trace, "softmax", node->src[0]->ne[0]);
            break;
            
        case GGML_OP_ROPE:
            // RoPE 位置编码 — SFU 执行
            trace_add_sfu(&trace, "rope", node->src[0]->ne[0]);
            break;
            
        case GGML_OP_NORM:
            // LayerNorm — SFU 执行
            trace_add_sfu(&trace, "layernorm", node->src[0]->ne[0]);
            break;
            
        case GGML_OP_GELU:
            // GELU 激活 — SFU 执行
            trace_add_sfu(&trace, "gelu", node->src[0]->ne[0]);
            break;
            
        case GGML_OP_ADD:
        case GGML_OP_MUL:
            // 逐元素运算 — Vector 单元执行
            trace_add_vector(&trace, node->name, node->src[0]->ne[0]);
            break;
            
        default:
            // 不支持的算子 → 交给 CPU backend 的回退
            return GGML_STATUS_FAILED;
        }
    }
    
    // ===== 步骤 3: 将 trace 编译为 NPU ISA 指令序列 =====
    // 这一步调用 Python 端的 NPUCompiler（通过 IPC）
    npu_isa_program_t program = npu_compile_trace(&trace);
    
    // ===== 步骤 4: 构建 DMA 描述符 =====
    // 把权重在 CPU 内存中的地址翻译成 NPU 能理解的 DRAM 地址
    npu_dma_desc_t * desc = npu_build_dma_descriptors(&trace);
    
    // ===== 步骤 5: 发送给 Python Model 执行 =====
    npu_result_t result = npu_device_execute(program, desc);
    
    // ===== 步骤 6: 读回结果 =====
    // 将 output buffer 从 NPU 端拷贝回 ggml tensor
    memcpy(node->data, result.output_buffer, result.output_size);
    
    return GGML_STATUS_SUCCESS;
}
```

### 3.1 关键理解：trace 是什么

trace 是一个中间表示（IR），不依赖 ggml 也不依赖 NPU ISA：

```c
struct npu_trace_entry {
    enum { GEMM, SFU, VECTOR } type;
    union {
        struct { int M, K, N, layer; void * weight_addr; } gemm;
        struct { char op[32]; int length; } sfu;
        struct { char op[32]; int length; } vector;
    };
};
```

trace 存在的原因是：**解耦 ggml 和 NPU ISA**。如果将来换 ExecuTorch，只需要改 ggml → trace 这一步，trace → NPU ISA 的逻辑不用动。

---

## 四、C → Python Model 通信方案

由于 Python Model 是现有的性能模拟器，我们需要让 C 代码能调用它。

### 方案选择

| 方案 | 优点 | 缺点 | 推荐？ |
|------|------|------|:---:|
| **Unix Socket** | 易调试，独立进程，崩溃不影响 llama | 每次 IPC 开销 ~10μs | ✅ 阶段1 |
| pybind11 | 零通信开销，C++ 直接调 Python | 编译复杂，调试困难 | 阶段2 |
| 管道 (pipe) | 最简单 | 单向，不适合双向通信 | — |
| 共享内存 | 零拷贝 | 同步复杂 | 阶段2 |

### 阶段1 推荐：Unix Socket

```
┌─────────────────┐         Unix Socket          ┌──────────────────┐
│   llama.cpp      │ ──────── connect ──────────→ │  npu_device.py    │
│   (C 进程)       │                               │  (Python 进程)    │
│                  │ ←──── "READY" ───────────── │                   │
│                  │                               │                   │
│  构建 trace      │                               │                   │
│  ├─ MUL_MAT      │ ──── trace (JSON/binary) ──→ │  编译 trace → ISA│
│  ├─ SOFTMAX      │                               │  → 模拟执行      │
│  └─ ROPE         │ ←── result (cycles+output) ── │  → 返回结果      │
│                  │                               │                   │
│  memcpy 结果     │                               │                   │
│  返回 llama.cpp  │ ─────── disconnect ─────────→ │  关闭连接         │
└─────────────────┘                               └──────────────────┘
```

通信协议（JSON，简单可读）：

```json
// C → Python: 发送 trace
{
    "cmd": "execute",
    "trace": [
        {"op": "gemm",  "M": 1, "K": 2560, "N": 4096, "layer": 0, "name": "Q_proj"},
        {"op": "gemm",  "M": 1, "K": 2560, "N": 256,  "layer": 0, "name": "K_proj"},
        {"op": "sfu",   "fn": "softmax", "len": 2560},
        {"op": "sfu",   "fn": "layernorm", "len": 2560},
        {"op": "gemm",  "M": 1, "K": 2560, "N": 9728, "layer": 0, "name": "FFN_gate"}
    ]
}

// Python → C: 返回结果
{
    "status": "ok",
    "cycles": 63855036,
    "tok_per_s": 15.7,
    "output_len": 10240,
    "output_hash": "a1b2c3d4"
}
```

### Python 端实现（参考代码）

```python
# ~/npu/ggml-npu/npu_device.py
import socket, json
from npu_sim import NPUSimulator, generate_qwen3b_trace

class NPUDevice:
    def __init__(self, config_path="config/npu_config.yaml"):
        self.sim = NPUSimulator(config_path)
    
    def execute(self, trace_entries):
        """执行 ggml 传来的 trace，返回结果"""
        # 将 ggml trace 转为 NPU trace
        trace = []
        for entry in trace_entries:
            if entry["op"] == "gemm":
                trace.append((entry["M"], entry["K"], entry["N"],
                             entry["layer"], entry["name"]))
        
        # 用已有的模拟器执行
        report = self.sim.simulate_decode(trace)
        
        return {
            "status": "ok",
            "cycles": int(report.decode_per_token_us * 1000),
            "tok_per_s": round(report.decode_tok_per_s, 1),
        }
    
    def serve(self, port=9999):
        """监听 Unix Socket"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(f"/tmp/npu_device_{port}.sock")
        sock.listen(1)
        
        while True:
            conn, _ = sock.accept()
            data = conn.recv(65536)
            request = json.loads(data)
            
            if request["cmd"] == "execute":
                result = self.execute(request["trace"])
                conn.send(json.dumps(result).encode())
            
            conn.close()
```

---

## 五、算子映射表：ggml_op → NPU 执行单元

| ggml_op | LLM 中用途 | 映射到 NPU 单元 | 频率 (每层) |
|---------|-----------|----------------|:---:|
| `GGML_OP_MUL_MAT` | Q/K/V/O 投影, FFN | **MXU (systolic array)** | 7次 |
| `GGML_OP_SOFT_MAX` | 注意力归一化 | **SFU** (分解: Vector→SFU→Vector→SFU) | 1次 |
| `GGML_OP_ROPE` | 位置编码 | **SFU** | 1次 |
| `GGML_OP_NORM` | LayerNorm | **SFU** | 2次 |
| `GGML_OP_GELU` | FFN 激活 | **SFU** | 1次 |
| `GGML_OP_SILU` | SwiGLU 激活 | **SFU** | 1次 |
| `GGML_OP_ADD` | 残差连接 | **Vector** | 2次 |
| `GGML_OP_MUL` | 门控乘法 | **Vector** | 1次 |
| `GGML_OP_RMS_NORM` | RMS Norm (Qwen用) | **SFU** | 2次 |
| `GGML_OP_RESHAPE` | 维度变换 | **不需要执行**（零开销） | — |
| `GGML_OP_VIEW` | 视图变换 | **不需要执行**（零开销） | — |
| `GGML_OP_PERMUTE` | 转置 | **不需要执行**（零开销） | — |
| `GGML_OP_CPY` | 数据类型转换 | CPU 或 DMA | 少量 |

> **关键设计决策**：RESHAPE/VIEW/PERMUTE/CPY 这些元操作在 NPU 上不产生计算。它们在 graph_compute 中被跳过，只在 DMA 描述符中调整地址偏移。

---

## 六、开发步骤（按顺序）

### 第 1 步：编译 llama.cpp，确认环境能跑

```bash
cd ~/llama.cpp
mkdir build && cd build
cmake .. -DGGML_METAL=OFF  # Apple Silicon 上关 Metal，先用纯 CPU 验证
make -j8
./bin/llama-cli -m ~/models/Qwen3-8B-Q4_K_M.gguf -p "你好" -n 10
# 应该能看到 CPU 推理输出
```

### 第 2 步：搭建 NPU backend 骨架

```bash
mkdir ~/npu/ggml-npu
cd ~/npu/ggml-npu
# 创建以下文件：
# - ggml-npu.cpp (后端实现)
# - ggml-npu.h   (头文件)
# - CMakeLists.txt
```

骨架代码只需要实现：
- 注册一个 backend，名字叫 "NPU"
- `graph_compute` 先只打印日志，不做实际计算
- 编译进 llama.cpp，确认 `--device NPU` 能被识别

### 第 3 步：实现 trace 收集

在 `graph_compute` 中遍历所有节点：
- 识别 `GGML_OP_MUL_MAT` → 记录 (M, K, N, layer, name)
- 识别 SFU 算子 → 记录 (op, length)
- 其他算子 → 记录并打印警告

验证：打印收集到的 trace，和 Qwen2.5-3B 的 7 个 GEMM 比对

### 第 4 步：实现 C → Python 通信

- 写 `npu_device_client.cpp`：Unix Socket 客户端
- 写 `npu_device.py`：Unix Socket 服务端
- 把 trace 以 JSON 格式发给 Python，收到结果

验证：Python 端打印收到的 trace，确认尺寸正确

### 第 5 步：集成 Python Model

- Python 端调用 `NPUSimulator.simulate_decode(trace)`
- 返回 cycles + tok/s
- C 端接收并打印

验证：看到性能数字和 `python3 npu_sim.py` 一致

### 第 6 步：端到端推理

- 实际执行 decode：把权重从 ggml tensor 传给 Python
- Python 模拟器执行，返回 output buffer
- C 端把结果写回 ggml tensor
- llama.cpp 继续采样和下一次 decode

验证：`llama-cli --device NPU` 能完成一次完整的 token 生成

---

## 七、常见问题

### Q: 为什么不在 graph_compute 里直接算，非要绕一道 Python？

因为 Python Model 已经实现了完整的性能模型（MXU v2 tiling-aware + SFU softmax 分解 + Vector + DMA bandwidth 建模）。用 C 重写一遍需要数周，且无法保证和 Python 模型一致。

阶段 2 换成 ExecuTorch Delegate 后，Python Model 的接口会被保留——它始终是 "golden reference"。

### Q: Socket 通信会不会成为瓶颈？

一次 decode 传输的 trace 约 196 个 GEMM × 5 个字段 × 8 字节 = ~8KB。Unix Socket 传输 8KB 约 10-20μs。

而一次 decode 的硬件执行时间约 30,000-64,000μs。Socket 开销占比 <0.1%，可以忽略。

### Q: 权重数据怎么处理？

阶段 1：权重不传给 NPU。Python Model 只需要 (M, K, N) 维度，不需要权重值。所以不需要传 1.5GB 的权重矩阵。

阶段 2：RTL 完成后，权重通过 PCIe DMA 直接加载到 NPU 的 DRAM。C 端只需要传权重在 CPU 内存中的物理地址。

### Q: 这个 backend 能被 llama.cpp 社区接受吗？

作为私有 fork 使用，不需要提交 PR。如果将来要贡献，需要：
1. NPU 硬件实际存在（不是 Python model）
2. 代码遵循 llama.cpp 的编码规范（无 AI 生成标记）
3. 通过 review 流程

参见 `~/llama.cpp/AGENTS.md`。

---

## 八、关键文件速查

| 文件 | 作用 |
|------|------|
| `~/llama.cpp/ggml/include/ggml-backend.h` | Backend 公开 API |
| `~/llama.cpp/ggml/src/ggml-backend-impl.h` | Backend 内部实现接口 |
| `~/llama.cpp/ggml/src/ggml-cpu/ggml-cpu.cpp` | CPU backend 参考实现（最简） |
| `~/llama.cpp/ggml/src/ggml-metal/ggml-metal.m` | Metal backend 参考（Apple GPU） |
| `~/npu/sim/npu_sim.py` | 我们的 NPU 模拟器 |
| `~/npu/sim/models/mxu.py` | MXU 性能模型 v2 |

---

## 九、当前项目状态（Phase 2）

### 架构

```
llama.cpp (CPU)                     npu_server.py (NPU)
──────────────                      ──────────────────
BLAS: ADD/ROPE/NORM/...             Socket batch compute
  │                                 ├─ 接收批量 MUL_MAT
  │  MUL_MAT ──Socket batch──►      ├─ 逐个计算（Phase2: 返零）
  │  ◄──结果────────────────        └─ 返回所有结果
  │
  └─ token 采样
```

### 双模运行

| 模式 | 命令 | 说明 |
|------|------|------|
| Phase 2 计算 | `python3 npu_server.py` | CPU 真把 MUL_MAT 发给 NPU |
| 性能仿真 | 自动随 batch 触发 | 跑 NPUSimulator（128×128 WC，3B→20 tok/s，7B→8.7 tok/s） |
| Hex stimulus | 自动生成 | `/tmp/npu_stimulus/` 供 RTL 验证用 |

### Dev Loop

```bash
# 单次
python3 npu_dev_loop.py --quick --force

# 监控模式（改 C++/Python 自动触发编译+测试+回归检测）
python3 npu_dev_loop.py --quick --watch

# 7B 仿真
python3 npu_dev_loop.py --sim-model 7B --force
```

### 关键文件

| 文件 | 作用 |
|------|------|
| `ggml-npu.cpp` | NPU backend：supports_op(MUL_MAT), batched graph_compute, hex dump |
| `ggml-npu.h` | 头文件 |
| `npu_server.py` | Phase 2 计算服务器 + NPU 性能仿真 |
| `npu_dev_loop.py` | 自动化编译→测试→对比→回归检测 |
| `qwen7b_sim.py` | 7B trace 生成器 |
| `CMakeLists.txt` | CMake 集成 |
| `/tmp/npu_stimulus/manifest.json` | Hex 刺激文件索引（RTL 就绪后直接用） |

### 性能基线（128×128 WC, INT4, 1000MHz）

| 模型 | Decode | tok/s | 瓶颈 |
|------|--------|-------|------|
| Qwen2.5-3B | 50,245 μs | 20 | MXU 94.7% |
| Qwen2.5-7B | 114,491 μs | 8.7 | MXU 94.8% |

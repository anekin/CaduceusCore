# CaduceusCore CV Model Zoo 规划

> 基于竞品调研 + MLPerf 行业基准，2026-06-21

---

## 一、竞品 CV 模型支持对比

与 CaduceusCore 同形态产品（M.2 2230 模组 / PCIe 卡 / USB 算力棒）的 CV 模型覆盖：

| 模型领域 | Hailo-8/8L (M.2) | Coral Edge TPU (USB/M.2) | Jetson Orin Nano (SOM) | 算能 BM1684X (PCIe) | **CaduceusCore** |
|----------|:---:|:---:|:---:|:---:|:---:|
| **图像分类** | YOLOv8-cls, ResNet-18/50 | MobileNetV1/V2 | 全系列 | ResNet/ResNeXt | 待定 |
| **目标检测** | YOLOv5/v8/v11 n/s/m/l, EfficientDet | EfficientDet-Lite0/1/2, SSD MobileNet | YOLO 全系, SSD | YOLO 全系, RetinaNet | 待定 |
| **实例分割** | YOLOv8-seg | DeepLabV3 | Mask R-CNN | YOLOv8-seg | 待定 |
| **姿态估计** | ❌ | ❌ | ✅ | ❌ | 待定 |
| **Transformer 视觉** | ❌ | ❌ | ViT, DETR | ❌ | **✅ 天然支持** |
| **LLM** | ❌ (需 Hailo-10H) | ❌ | ✅ | ✅ Llama/Qwen | **✅ 双栈** |

### 关键发现

1. **Hailo-8（最直接竞品，同形态 M.2 2230）**：只支持 CNN，无 Transformer。Model Zoo 覆盖 YOLO + ResNet + MobileNet 主流系列，通过 Dataflow Compiler 编译 ONNX 模型。Hailo-10H 才是 LLM 芯片，是**不同芯片**。

2. **Coral Edge TPU（USB 算力棒形态参考）**：仅支持 TensorFlow Lite INT8 模型，模型种类极度受限（MobileNet/EfficientDet-Lite/DeepLab），编译器严格拒绝不支持算子。

3. **算能 BM1684X（唯一国内竞品，同形态 PCIe）**：通过 TPU-MLIR 工具链支持 CV + LLM 双栈，CV 模型覆盖 YOLO/ResNet/MobileNet/EfficientNet 等主流分类/检测网络，但缺乏 Transformer 视觉模型。

4. **CaduceusCore 的差异化**：systolic array 天然支持 im2col→GEMM（CNN），也支持原生 self-attention（Transformer）。这是 Hailo-8/Coral 做不到的。Transformer 视觉模型（ViT/DETR/Swin）是我们的**独家优势**。

---

## 二、MLPerf Edge Inference 基准模型

MLPerf 是行业标准推理基准，Edge Closed 组涵盖以下模型类型：

| 模型 | 任务 | 领域 | 对应 B类/C类 |
|------|------|------|:--:|
| ResNet-50 | 图像分类 | Vision | B类 |
| RetinaNet | 目标检测 | Vision | B类 |
| 3D-UNet | 医学图像分割 | Vision/Medical | C类 |
| BERT-Large | NLP | Language | B类 |
| GPT-J | LLM生成 | Language | C类 |

CaduceusCore 作为 **LLM+CV 双栈** NPU，覆盖 MLPerf 的 CV + LLM 两个维度的基准是合理目标。

---

## 三、Model Zoo 分层规划

### B类 (Baseline) — 必须支持，对标竞品基本盘

从竞品分析和 MLPerf 基准中提取，每款竞品都支持的"入场券"级模型：

| # | 模型 | 任务 | 参数量 | 输入 | 关键 ops | 选取理由 |
|:--:|------|------|:---:|------|------|------|
| B1 | **MobileNetV3-Small** | 分类 | 2.5M | 224×224 | Conv2D+ReLU+Pooling | Coral 基准，极轻量，验证端侧推理性 |
| B2 | **ResNet-18** | 分类 | 11.7M | 224×224 | Conv2D+BatchNorm+ReLU | MLPerf + 所有竞品支持，卷积算子全验证 |
| B3 | **YOLOv8n** | 检测 | 3.2M | 640×640 | Conv2D+SiLU+Concat | Hailo + 算能主推，检测赛道必选 |
| B4 | **EfficientDet-Lite0** | 检测 | 3.2M | 320×320 | Conv2D+BiFPN+SiLU | Coral + Hailo 都支持，对标 USB 棒场景 |

### C类 (Competitive) — 差异化优势，展示双栈 + Transformer 能力

B类覆盖了竞品同质化能力，C类是我们独有的、竞品做不到或做不好的：

| # | 模型 | 任务 | 参数量 | 输入 | 关键 ops | 选取理由 |
|:--:|------|------|:---:|------|------|------|
| C1 | **ViT-B/16** | 分类 | 86M | 224×224 | Self-Attn+MLP+LayerNorm | **独家**，Hailo/Coral 不支持 Transformer 视觉 |
| C2 | **ResNet-50** | 分类 | 25.6M | 224×224 | Conv2D+Bottleneck+BN | MLPerf 基准，工业客户评估标准 |
| C3 | **YOLOv8s** | 检测 | 11.2M | 640×640 | Conv2D+SiLU+C2f | 压力测试（比 Nano 更大的检测模型） |
| C4 | **EfficientNet-B0** | 分类 | 5.3M | 224×224 | DepthwiseConv+SE+Swish | 现代 CNN 架构代表，算能支持 |
| C5 | **YOLOv8s-seg** | 分割 | ~12M | 640×640 | Conv2D+上采样+Mask | 分割赛道，超越 Coral 的能力展示 |

### 汇总

| 分类 | LLM (已有) | CV B类 (新增) | CV C类 (新增) | 合计 |
|------|:----:|:----:|:----:|:----:|
| 模型数 | 5 | 4 | 5 | 14 |
| 覆盖任务 | 文本生成 | 分类/检测 | 分类/检测/分割 | 6种 |
| 关键能力 | Self-Attn, GELU | Conv2D, ReLU, Pool | Self-Attn(CV), SE, BiFPN | 全算子 |

---

## 四、对 Arc Model / Func Model 的影响

| 层面 | 当前状态 | 需新增 |
|------|---------|--------|
| **Arc Model** | 仅 LLM：GGUF→INT4→MXU GEMM→cos_sim+tok/s | + CV 模型加载 (ONNX/PyTorch) + im2col→GEMM 性能模型 + Conv2D 量化精度 |
| **Func Model** | MXU(INT4×INT8), SFU(GELU/Softmax) | + im2col 引擎 + ReLU/LeakyReLU/SiLU/Swish + MaxPool2D/AvgPool2D + Concat/Upsample |
| **Model Zoo** | `qwen2.5-1.5b/3b/7b, qwen3-8b, gemma-4-12b` | B1-B4 + C1-C5 = 9 个 CV 模型 |

---

## 五、建议实施顺序

1. **B1 MobileNetV3-Small** — 最轻量，快速跑通 Conv2D 全链路（im2col + GEMM + ReLU + Pool）
2. **B3 YOLOv8n** — 验证检测模型完整流程图（Backbone→Neck→Head）
3. **B2 ResNet-18** — 验证残差连接 + BatchNorm folding
4. **C1 ViT-B/16** — 展示独家 Transformer+CV 能力，与 LLM 共享 Self-Attention 算子
5. **C2-C5** — 扩大规模与任务覆盖

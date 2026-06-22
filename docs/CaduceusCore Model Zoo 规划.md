# CaduceusCore Model Zoo 规划（LLM + CV）

> 基于竞品调研 + MLPerf 行业基准，2026-06-21
> **v2.0**：新增 LLM 分层规划

---

## 一、Model Zoo 分层逻辑

| 层级 | 含义 | 选取标准 |
|:----:|------|------|
| **B类** | Baseline — 入场券 | 竞品都支持、MLPerf 基准、客户评估必问。不支持就不算"能跑 LLM/CV 的 NPU" |
| **C类** | Competitive — 差异化 | 竞品跑不了或跑不好的，展示 CaduceusCore 独有架构优势 |

---

# Part 1: LLM Model Zoo

## 1.1 竞品 LLM 支持对比

同形态产品中唯一支持 LLM 的竞品是 **Hailo-10H**（需注意：Hailo-8 不支持 LLM，Hailo-10H 是**不同芯片**，从芯片层面就得分两款。我们一颗芯片搞定 CV+LLM）：

| 模型 | Hailo-10H | 算能 BM1684X | Jetson Orin Nano | Apple M5 (CPU) | **CaduceusCore** |
|------|:---:|:---:|:---:|:---:|:---:|
| **Llama 3.2-1B** | ✅ | ✅ | ✅ | ✅ | 待定 |
| **Llama 3.2-3B** | ✅ | ✅ | ✅ | ✅ | 待定 |
| **Qwen2.5-1.5B** | ✅ | ✅ | ✅ | ✅ | ✅ 已有 |
| **Qwen2.5-3B** | ⚠️ | ✅ | ✅ | ✅ | ✅ 已有 |
| **DeepSeek-R1-1.5B** | ✅ | ❌ | ✅ | ✅ | 待定 |
| **Gemma 2 2B** | ✅ | ❌ | ✅ | ✅ | ❌ |
| **Phi-3.5-mini (4B)** | ✅ | ❌ | ✅ | ✅ | 待定 |
| **Llama 3.1-8B** | ❌ | ✅ | ✅ | ⚠️ 慢 | ✅ 已有(同类) |
| **Mistral-7B** | ❌ | ⚠️ | ✅ | ⚠️ | 待定 |
| **Mixtral 8×7B (MoE)** | ❌ | ❌ | ⚠️ | ❌ | 待定 |

### 关键发现

1. **Hailo-10H 的覆盖是分芯片的**：CV 用 Hailo-8、LLM 用 Hailo-10H，客户要买两颗芯片。我们一颗搞定——但 LLM 模型列表至少要对齐 Hailo-10H，才能说"我们一颗顶两颗"。

2. **当前 5 个模型全是 Qwen/Gemma 系**——缺 Llama（全球最通用的开源 LLM），在海外市场评估中会被质疑。

3. **DeepSeek-R1**：我们 RTL 项目最初以验证 CodeV-R1 方法为目标，但 Model Zoo 里没有 DeepSeek。这是目标与验证脱节。

4. **Phi-3.5-mini**：Microsoft 的端侧标杆，Hailo-10H 已支持。4B 参数但实测效果接近 7B，是 M.2 模组最理想的体量之一。

---

## 1.2 MLPerf LLM 基准

| 模型 | 参数量 | 任务 | 对应层级 |
|------|:---:|------|:--:|
| BERT-Large | 340M | NLP（非生成） | B类 |
| GPT-J | 6B | 文本生成 | B类 |
| Llama 2-70B | 70B | 文本生成（数据中心） | — 不适用 |

CaduceusCore 定位 3B 级边缘推理，GPT-J (6B) 是 MLPerf 最接近我们定位的 LLM 生成基准。

---

## 1.3 LLM 分层规划

### B类 (Baseline) — 必须支持

| # | 模型 | 参数量 | hidden | layers | 架构 | 选取理由 |
|:--:|------|:---:|:---:|:---:|------|------|
| B1 | **Llama 3.2-1B** | 1.2B | 2048 | 16 | Dense, GQA | 全球最通用的 1B 级模型，Hailo-10H 必选 |
| B2 | **Qwen2.5-1.5B** ✅ | 1.5B | 1536 | 28 | Dense, GQA | 中文市场标杆，Hailo-10H + 算能双选 |
| B3 | **Llama 3.2-3B** | 3.2B | 3072 | 28 | Dense, GQA | 3B 甜点位，MRD 目标体量，Hailo-10H 支持 |
| B4 | **Phi-3.5-mini** | 3.8B | 3072 | 32 | Dense, GQA | 微软端侧标杆，4B 体量接近 7B 效果 |

### C类 (Competitive) — 差异化优势

| # | 模型 | 参数量 | hidden | layers | 架构 | 选取理由 |
|:--:|------|:---:|:---:|:---:|------|------|
| C1 | **Qwen2.5-3B** ✅ | 3.1B | 2560 | 28 | Dense, GQA | 已有，保留 |
| C2 | **DeepSeek-R1-Distill-1.5B** | 1.5B | 1536 | 28 | Dense, Reasoning | RTL 验证目标，推理链模型（不同 decode 模式） |
| C3 | **Qwen3-8B** ✅ | 8.2B | 4096 | 32 | Dense, GQA | 已有，最新 Qwen 旗舰小模型，带宽压力测试 |
| C4 | **Llama 3.1-8B** | 8B | 4096 | 32 | Dense, GQA | 全球 8B 标杆，对标 GPT-J (MLPerf) |
| C5 | **Gemma 4-12B** ✅ | 12B | 4096 | 40 | Dense, GQA | 已有，最大规模压力测试 |
| C6 | **Mistral-7B** | 7.3B | 4096 | 32 | Dense, SWA | 欧洲旗舰，SwishGLU 不同 FFN 结构 |

---

## 1.4 LLM 汇总

| 分类 | 保留 (已有) | 新增 | 移除 | 最终 |
|------|:---:|:---:|:---:|:--:|
| B类 | Qwen2.5-1.5B | Llama 3.2-1B, Llama 3.2-3B, Phi-3.5-mini | Qwen2.5-7B(→C4替代) | 4 |
| C类 | Qwen2.5-3B, Qwen3-8B, Gemma-4-12B | DeepSeek-R1-1.5B, Llama 3.1-8B, Mistral-7B | — | 6 |
| **合计** | 4 | 6 | 1 | **10** |

### 与竞品覆盖对比

| | Hailo-10H | 算能 BM1684X | **CaduceusCore (规划)** |
|------|:---:|:---:|:---:|
| 支持模型数 | ~6 | ~5 | **10** |
| 国产模型 | Qwen, DeepSeek | Qwen, ChatGLM | Qwen, DeepSeek |
| 海外模型 | Llama, Phi, Gemma | Llama | Llama, Phi, Mistral, Gemma |
| MoE | ❌ | ❌ | 待定(Mistral可选) |
| 推理链模型 | DeepSeek | ❌ | DeepSeek |

---

# Part 2: CV Model Zoo

## 2.1 竞品 CV 模型支持对比

| 模型领域 | Hailo-8/8L (M.2) | Coral Edge TPU (USB/M.2) | Jetson Orin Nano (SOM) | 算能 BM1684X (PCIe) | **CaduceusCore** |
|----------|:---:|:---:|:---:|:---:|:---:|
| **图像分类** | YOLOv8-cls, ResNet-18/50 | MobileNetV1/V2 | 全系列 | ResNet/ResNeXt | 待定 |
| **目标检测** | YOLOv5/v8/v11, EfficientDet | EfficientDet-Lite, SSD | YOLO 全系 | YOLO 全系 | 待定 |
| **实例分割** | YOLOv8-seg | DeepLabV3 | Mask R-CNN | YOLOv8-seg | 待定 |
| **Transformer 视觉** | ❌ | ❌ | ViT, DETR | ❌ | **✅ 独家** |

CaduceusCore 的核心差异：systolic array 天然支持 im2col→GEMM（CNN）**和** self-attention（Transformer ViT）。Hailo-8/Coral 的数据流架构做不了 Transformer。

---

## 2.2 CV 分层规划

### B类 (Baseline)

| # | 模型 | 任务 | 参数量 | 选取理由 |
|:--:|------|------|:---:|------|
| B1 | **MobileNetV3-Small** | 分类 | 2.5M | Coral 基准，最轻量端侧验证 |
| B2 | **ResNet-18** | 分类 | 11.7M | MLPerf + 所有竞品支持 |
| B3 | **YOLOv8n** | 检测 | 3.2M | Hailo + 算能主推 |
| B4 | **EfficientDet-Lite0** | 检测 | 3.2M | Coral + Hailo 双选 |

### C类 (Competitive)

| # | 模型 | 任务 | 参数量 | 选取理由 |
|:--:|------|------|:---:|------|
| C1 | **ViT-B/16** | 分类 | 86M | 🔑 Transformer 视觉，竞品跑不了 |
| C2 | **ResNet-50** | 分类 | 25.6M | MLPerf 工业基准 |
| C3 | **YOLOv8s** | 检测 | 11.2M | 检测压力测试 |
| C4 | **EfficientNet-B0** | 分类 | 5.3M | 现代 CNN 架构标杆 |
| C5 | **YOLOv8s-seg** | 分割 | ~12M | 分割赛道 |

---

# Part 3: 完整 Model Zoo 总览

| | LLM | CV | 合计 |
|------|:---:|:---:|:---:|
| B类 | 4 | 4 | **8** |
| C类 | 6 | 5 | **11** |
| **总计** | **10** | **9** | **19** |

### 覆盖维度

| 维度 | 覆盖情况 |
|------|------|
| **任务类型** | 文本生成 / 推理链 / 分类 / 检测 / 分割 / 姿态(待扩展) |
| **架构多样** | Dense FFN / SwishGLU / GQA / SWA / CNN / Transformer |
| **市场覆盖** | 中国(Qwen/DeepSeek) + 全球(Llama/Phi/Mistral/Gemma) |
| **规模梯度** | 1M (MobileNet) → 12B (Gemma-4) |
| **MLPerf 对齐** | ResNet-18/50 + GPT-J + BERT(待评估) |

---

# Part 4: 对 Arc Model / Func Model 的影响

| 层面 | 当前 | 需新增 |
|------|------|--------|
| **Arc Model** | GGUF → INT4 → cos_sim + tok/s (仅 LLM) | ONNX/PyTorch CV 模型加载 + im2col→GEMM + Conv2D 精度 |
| **Func Model** | MXU + SFU(GELU/Softmax) + MMIO | im2col 引擎 + ReLU/SiLU/Swish + Pool2D + Concat/Upsample |
| **GGUF 模型** | 5 个 (全 Qwen/Gemma) | + Llama/Phi/DeepSeek/Mistral ≈ 10 个 |
| **CV 模型** | 0 | + ONNX/pt 加载器 + 9 个模型 |

---

# Part 5: 实施路线图

```
Phase 1: CV 链路跑通 (当前)
  B1 MobileNetV3-Small → im2col + MXU GEMM + ReLU + Pool → 首个性别 CV 模型
  B3 YOLOv8n → 完整 Backbone→Neck→Head 流程
  B2 ResNet-18 → 残差 + BatchNorm

Phase 2: 独家优势展示
  C1 ViT-B/16 → Transformer CV, 竞品无此能力
  同时跑通 LLM B1-B4 (新增 Llama 3.2 + Phi-3.5)

Phase 3: 规模扩展
  C2-C5 (ResNet-50, YOLOv8s, EfficientNet-B0, YOLOv8s-seg)
  C2-C6 (DeepSeek-R1, Llama 8B, Mistral 7B)
```

---

# 附录：现有模型 vs 规划变更

| 模型 | 当前状态 | 规划 |
|------|:--:|------|
| Qwen2.5-1.5B | ✅ 已有 | 保留 (B2) |
| Qwen2.5-3B | ✅ 已有 | 保留 (C1) |
| Qwen2.5-7B | ✅ 已有 | **移除** — 由 Llama 3.1-8B 替代 (同体量全求标杆) |
| Qwen3-8B | ✅ 已有 | 保留 (C3) |
| Gemma-4-12B | ✅ 已有 | 保留 (C5) |
| Llama 3.2-1B | ❌ | 新增 (B1) |
| Llama 3.2-3B | ❌ | 新增 (B3) |
| Phi-3.5-mini | ❌ | 新增 (B4) |
| DeepSeek-R1-1.5B | ❌ | 新增 (C2) |
| Llama 3.1-8B | ❌ | 新增 (C4) |
| Mistral-7B | ❌ | 新增 (C6) |

# fm-e2e-qwen-cv-software-stack — Work Plan
## TL;DR (For humans)
> Summary: 基于软件栈 (`ggml-npu` + Host Runtime + `device_server` / `fm://python`) 真跑 Qwen2.5-3B 全 36 层 forward pass 和 MobileNetV3-Small CV 推理的端到端验证。双轨并行：Track A 扩展现有 Qwen signoff 到全层，Track B 从零搭建 CV 软件栈跑通路径。
> Phase constraint: Func Model 软件栈路径 (`fm://python`) 优先；mock 路径做退化覆盖。Spike 固件路径为可选扩展。RTL 真实传输不在范围内。
> Deliverables:
> - Track A: Qwen 36 层 forward 跑通，逐层 hidden state 与 llama.cpp CPU golden 比对，最终 logits/token ID 一致。
> - Track B: MobileNetV3-Small ONNX → command IR → Host Runtime → device_server 全链路跑通，输出 logits 与 ONNX Runtime golden 比对。
> Effort: L — 18 todos 跨 4 waves（不含可选 Wave 4）。Track A 和 Track B 各自独立可并行。工时估算：Wave 0 约 2-3h，Wave 1 约 4-6h，Wave 2 约 6-8h，Wave 3 约 2h，Wave 4（可选）约 1-2h。
> Risk: Medium — Track B CV 栈需要新建 ONNX→command IR 前端，设计决策多但风险可控；Track A 以现有 signoff 为基础，扩展现有 infrastructure。

## Scope

### In-Scope
Track A — Qwen2.5-3B 全层 forward:
1. 生成 36 层完整 golden reference（llama.cpp CPU 路径导出 per-layer hidden states + 最终 logits/tokens）。
2. 编写 36 层 forward runner：embeddings → 36×blk → output norm → lm_head → token id。
3. 逐层 hidden state 比对（cos_sim 和 max_abs_diff），首层和末层必须达标，中间层允许部分跳过。
4. 最终 logits/token ID 比对（top-5 一致性 + 精确 token 匹配）。
5. KV cache 分配、读写正确性验证（至少覆盖 seq_len=128）。
6. device_server 自动化：runner 内嵌生命周期管理，无人值守可跑。
7. 支持 mock:// 降级路径（不做 NPU offload 但保证全部 op 可遍历）。

Track B — CV 软件栈（MobileNetV3-Small 首发）:
8. 实现 ONNX → Caduceus command IR 转换器（Conv → im2col → MMUL，SFU 算子映射）。
9. 搭建 CV 推理 host runner：加载 ONNX 模型 → 转换 → Host Runtime 提交 → device_server 执行。
10. device_server 端新增 CV 模型加载和执行路径（复用已有 MMIO/Buffer/Fence）。
11. 验证 MobileNetV3-Small top-5 分类结果与 ONNX Runtime 一致。
12. 覆盖 MobileNetV3-Small 全图 op：Conv、HardSwish、HardSigmoid、SE block (ReduceMean + Conv + Relu + HardSigmoid + Mul)、GlobalAveragePool。

Track A+B 共享:
13. CI 可复现：signoff runner 包含 device_server 自动启动/停止、model 下载检查、构建验证。
14. 证据文件输出到 `.omo/evidence/`。

### Must NOT Do
- Do NOT 修改 RTL datapath（`rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/soc/`）。
- Do NOT 实现 RTL 真实传输（`fm://rtl`）。
- Do NOT 扩展 ExecuTorch delegate。
- Do NOT 追加 CV 模型（仅 MobileNetV3-Small，YOLOv8/ResNet/ViT 为下一阶段）。
- Do NOT 更改 ggml-npu 与 Host Runtime 之间的 public ABI。
- Do NOT 在 spyke_host.py 或 signoff gate 里硬编码 36——所有层数从配置/模型文件读取。

## Verification Strategy
- **TDD**: 每个 todo 先写 failing test/evidence 文件，再实现，再验证 green。
- **Unit layer**: Python pytest for new modules (ONNX→command IR converter, CV host runner)。
- **Integration layer**: 
  - Track A: llama.cpp CPU path 导出 36 层 hidden states → NPU path 导出 36 层 → per-layer cos_sim 对比。
  - Track B: ONNX Runtime inference → NPU path inference → top-5 matching。
- **Regression layer**: 每个 todo 结束后 rerun 已有 Qwen3B signoff 确保未退化。
- **Evidence policy**: 每个 todo 的验证命令和证据路径在 plan 中明确写出。

## Guardrail Traceability
| Guardrail | Reason |
|-----------|--------|
| 不修改 RTL 任何 `.v` 文件 | 纯 Func Model 软件栈验证 |
| mock:// 路径必须可用 | CI 无 device_server 时仍可跑 |
| 不使用硬编码层数 | Qwen2.5-3B 有 28/36 两种 variant，需自适应 |
| CV 只跑 MobileNetV3-Small | 先验证端到端可行性，再扩展多模型 |
| 不引入新第三方 Python 包到 requirements.txt（除 onnxruntime 外） | onnxruntime 是 CV golden 必需品；其余用已有依赖 |

## Dependency Matrix

### Track A — Qwen Full Forward
| Todo | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| A1 (36 层 golden 生成) | None | A2 | B1, B2 |
| A2 (embedding+lm_head runner) | A1 | A3, A4 | — |
| A3 (per-layer hidden compare) | A2 | A5 | — |
| A4 (KV cache 验证) | A2 | — | A3 |
| A5 (device_server 自动化) | None | A2, A3, A4, S1 | A1, B1, B2 |
| A6 (mock:// 降级) | A2 | — | A5 |

### Track B — CV Software Stack
| Todo | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| B1 (ONNX→command IR converter) | None | B2, B3 | A1, A2 |
| B2 (ONNX Runtime golden ref) | None | B4 | A1 |
| B3 (CV host runner) | B1 | B4, B5 | — |
| B4 (device_server CV path) | B3 | — | A5 |
| B5 (top-5 correctness verify) | B3, B4 | — | — |
| B6 (MobileNetV3 full graph test) | B3, B4, B5 | — | — |

### Shared
| Todo | Depends On | Blocks | Can Parallelize With |
|------|-----------|--------|---------------------|
| S1 (CI reproducibility) | A5, B4 | — | all |
| S2 (evidence aggregation) | all above | — | — |

### Critical Path
```
Track A:  A1 → A2 → A3/A4  (A5 并行，A5 完成后 A2/A3/A4 可以用 fm://python 自动启动)
Track B:  B1 → B3 → B4 → B5 → B6  (B2 并行于 B1)
  → S1 → S2
```

## TODOs

- [x] 1. **A5: `sim/signoff/` device_server lifecycle fixture** — Runner 自动启动/停止 `device_server`，无需手动 `python -m sim.device_server`
   - Acceptance: `PYTHONPATH=sim:gen python3 scripts/run_qwen3b_software_signoff.py positive --device fm://python` 无需预先手动启动 device_server 即可通过 `full_shape_blk0`
   - QA: happy=`managed_device_server()` 在 `with` 块内 socket 可达；failure=device_server 启动后 5s 超时返回 BLOCKED
   - Commit: `feat(signoff): add managed device_server fixture for fm://python gates`

- [x] 2. **A1: `scripts/gen_qwen_full_golden.py` 生成 36 层 golden reference**
   - 用 llama.cpp CPU 路径 (`CADUCEUS_DEVICE` unset) 运行 `dump_hidden_states`，对每一层 dump 隐藏层张量。输出 **两种格式**：(1) 合并 `.npz` 文件 `qwen-36l-golden.npz` 包含 per-layer `hidden_states`, `logits`, `tokens`；(2) 逐层 `expected_l{N}.npz` 与现有 `scripts/run_36layer_checkpoint.py` 兼容。支持 `--layers N` 参数。
   - Acceptance: 运行 `PYTHONPATH=sim python3 scripts/gen_qwen_full_golden.py --model <path> --layers 36 --output .omo/evidence/`，产出 `qwen-36l-golden.npz`（≥100MB）和 36 个 `expected_l0.npz..expected_l35.npz`，且 `scripts/run_36layer_checkpoint.py --checkpoint-dir .omo/evidence/` 通过。
   - QA: happy=两种格式均包含所有 36 层的 `l_out_{N}` key；failure=llama CLI 崩溃时脚本 exit≠0 并给出可读错误
   - Commit: `feat(signoff): add 36-layer golden reference generator for Qwen full forward`

- [x] 3. **A2: `sim/signoff/qwen3b_full_forward.py` embedding + lm_head runner**
   - 从 `qwen3b_signoff_io.py` 的 `_run_dump_hidden_states` 提取公共逻辑，新增 `run_full_forward()` 函数：tokenize→embedding→36×blk→norm→lm_head→argmax。通过 ggml-npu backend + device_server 执行完整 36 层 compute graph。
   - Acceptance: 至少 1 token 的 decode 输出 text 与 CPU 参考一致。
   - QA: happy=单 token gen 文本匹配；failure=embedding/lm_head op 不支持时正常 fallback 并记录
   - Commit: `feat(signoff): add full 36-layer forward runner with embedding and lm_head`

- [x] 4. **A3: `sim/signoff/qwen3b_per_layer_compare.py` 逐层 hidden state 比对**
   - 加载 golden npz（来自 A1），对 runner（A2）产出的每层 hidden state 计算 cos_sim 和 max_abs_diff。首层（l_out_0）和末层（l_out_35）必须达标；中间层记录但允许 skip。输出 JSON evidence 包含每层 metrics。
   - Acceptance: 跑 `PYTHONPATH=sim python3 sim/signoff/qwen3b_per_layer_compare.py --golden .omo/evidence/qwen-36l-golden.npz --device fm://python`，l_out_0 cos_sim≥0.99 且 max_abs_diff≤1e-3。
   - QA: happy=首末层达标；failure=golden npz 缺失时给出 "run A1 first" 提示
   - Commit: `feat(signoff): add per-layer hidden state comparison for 36-layer forward`

- [x] 5. **A4: KV cache correctness — 扩展 `gate_multi_token_decode_with_kv` 到 seq_len≥128**
   - 将 `multi_token_decode_with_kv` gate 的 `n_predict` 从 3 扩展到 128（或通过配置参数），验证 KV cache 在长序列下不溢出、不串扰。对比中间 token 的 hidden states。
   - Acceptance: seq_len=128 时 128 个 token 的文本生成稳定，无 OOM/segfault。
   - QA: happy=`n_predict=128` 全部 token 生成完毕；failure=OOM 时 gracefully 降级到 `n_predict=8`
   - Commit: `test(signoff): extend multi-token decode KV cache test to seq_len=128`

- [x] 6. **A6: mock:// 降级路径验证**
   - 确认 36 层 forward 在 `--device mock://` 下也能完成 op 遍历（不验证 NPU 数值正确性，仅验证 graph 可遍历/无 crash）。mock:// 路径下后端不提交到 NPU，所有 op 走 CPU fallback 记录。
   - Acceptance: `PYTHONPATH=sim python3 sim/signoff/qwen3b_full_forward.py --device mock:// --layers 36` 退出码 0，stderr 中 `[NPU] OP node` 行总数（含 CPU fallback）≥612。
   - QA: happy=总 op node 行≥612；failure=中途 crash 时给出最后执行的 layer 编号；device_server 不可用时 graceful skip
   - Commit: `test(signoff): verify mock:// path traverses full 36-layer graph`

- [x] 7. **B1: `sim/cv/cv_command_ir.py` ONNX → Caduceus command IR 转换器**
   - 基于 `sim/cv/conv_mapper.py`（Conv→im2col→GEMM）和 `sim/cv/onnx_importer.py`（ONNX 拓扑解析），生成 command IR blob。复用 `software/compiler/command_ir.h` 的编码格式。覆盖：Conv (pointwise/depthwise)、HardSwish、HardSigmoid、GlobalAveragePool、SE block。
   - Acceptance: `PYTHONPATH=sim python3 -m pytest sim/tests/test_cv_command_ir.py -q` — 至少 8 个测试通过。
   - QA: happy=imported、partitioned、generated blob passes `CommandBlob.decode()`；failure=不支持 op 时 raise `UnsupportedCVOp` 并给出 op 名
   - Commit: `feat(cv): add ONNX-to-command-IR converter for MobileNetV3`

- [x] 8. **B2: `scripts/gen_cv_golden.py` ONNX Runtime golden reference 生成器**
   - 用 `onnxruntime.InferenceSession` 跑 MobileNetV3-Small 推理（随机/真实图片输入），保存 top-5 分类结果和中间层 logits 到 JSON/NPZ。
   - Acceptance: `PYTHONPATH=sim python3 scripts/gen_cv_golden.py --model assets/mobilenetv3_small.onnx --output .omo/evidence/cv-golden.json` 退出码 0。
   - QA: happy=输出包含 `top5_indices` 和 `top5_logits`；failure=ONNX 文件缺失时 exit≠0
   - Commit: `feat(cv): add ONNX Runtime golden reference generator for MobileNetV3`

- [x] 9. **B3: `sim/cv/cv_host_runner.py` CV 推理 host runner**
   - Host 侧 load ONNX → convert to command IR (B1) → Host Runtime API (`cadDeviceOpen`, `cadBufferAlloc`, `cadQueueSubmit`) → device_server execution → read results。串联完整 pipeline。
   - Acceptance: 通过 `fm://python` 成功提交 MobileNetV3 的首个 Conv layer 执行，buffer read 返回非零张量。
   - QA: happy=`cadQueueSubmit` 成功 + buffer read 有数据；failure=command IR 格式错误时 Host Runtime 返回 `CAD_ERROR_INVALID_ARGUMENT` 并被 runner 正确捕获
   - Commit: `feat(cv): add CV host runner wiring ONNX via Host Runtime to device_server`

- [x] 10. **B4: `sim/device_server.py` 中新增 CV 模型执行路径**
    - device_server 现有的 `_execute_blob()` 是通过 FuncModel firmware 执行的；CV 需要新增 `_execute_cv_blob()` 路径，直接调用 FuncModel MXU/SFU/Vector handler（绕过 firmware ring buffer，因为 CV 没有 RISC-V 固件）。或者复用 firmware dispatch 但用合成 descriptors。
    - Acceptance: 提交 MobileNetV3 全图后，device_server 返回 `DeviceStatus.OK`。
    - QA: happy=`_execute_cv_blob` 对所有 conv/sfu/elementwise 返回 OK；failure=opcode 不支持时返回 `DeviceStatus.UNSUPPORTED_OPERATION`
    - Commit: `feat(device_server): add CV model execution path bypassing firmware ring buffer`

- [x] 11. **B5: `sim/tests/test_cv_e2e.py` top-5 正确性验证**
    - 比对 Host Runner (B3) 产出的分类结果与 ONNX Runtime Golden (B2) 的 top-5，要求 top-5 集合一致。
    - Acceptance: `PYTHONPATH=sim python3 -m pytest sim/tests/test_cv_e2e.py -q`，top-5 matching 测试通过。
    - QA: happy=top-5 完全一致；failure=数值差异过大（>0.01 relative）时标记为 FAIL 并输出 diff
    - Commit: `test(cv): add end-to-end top-5 accuracy verification for MobileNetV3`

- [x] 12. **B6: MobileNetV3 全图回归测试**
    - 对 MobileNetV3-Small 的全部 52 层（~20M MACs）通过 Host Runner 执行，验证全程无 crash，所有 op 被覆盖。
    - Acceptance: 全图执行成功，`npu_ops_executed` = 图中所有 NPU-dispatched op 数量。
    - QA: happy=全图 op count 与 ONNX node count 一致；failure=中途 crash 时 runner 报告到哪个 layer 停止
    - Commit: `test(cv): add full MobileNetV3-Small graph regression through software stack`

- [x] 13. **S1: CI reproducibility — `scripts/run_e2e_software_signoff.sh`**
    - 统合 Qwen + CV 的所有 signoff gate 到一个入口脚本，包含 model 存在性检查、device_server 生命周期管理、PYTHONPATH 设置。支持 `--device fm://python|mock://`。
    - Acceptance: 从干净 clone 运行 `bash scripts/run_e2e_software_signoff.sh` 跑通 mock:// 路径全程。
    - QA: happy=脚本退出码 0；failure=缺少 GGUF/ONNX 时 exit≠0 给出明确安装指引
    - Commit: `ci(signoff): add unified end-to-end software signoff script`

- [x] 14. **S2: evidence aggregation — `scripts/aggregate_e2e_signoff.py`**
    - 扩展或新建聚合器，覆盖 Track A（Qwen per-layer + logits）和 Track B（CV top-5 + full graph）的证据文件。输出统一 JSON 报告。
    - Acceptance: 聚合器在 `--strict` 下退出码 0（所有证据 present + hashes 匹配）。
    - QA: happy=所有 task evidence 通过；failure=缺失证据时退出码≠0 并列出缺失项
    - Commit: `feat(signoff): add e2e signoff evidence aggregator for Qwen+CV`

- [x] 15. **P1: `scripts/gen_mobilenetv3_onnx.sh` ONNX 模型自动下载**
    - 若 `assets/mobilenetv3_small.onnx` 不存在，从公开 URL 下载（或提示用 torch export 生成）。
    - Acceptance: 脚本幂等（已存在时不覆盖）。Commit: `chore(cv): add MobileNetV3 ONNX download script`

- [x] 16. **P2: `PYTHONPATH` 统一化**
    - 所有 signoff runner 自动 set `PYTHONPATH=sim:gen:software`，消除 QA 中发现的 `PYTHONPATH=sim` 路径不全问题。
    - Acceptance: 无需手动 export PYTHONPATH 即可运行任意 runner。
    - Commit: `fix(signoff): auto-set PYTHONPATH in all signoff runners`

- [x] 17. **P3: CV trace 回归保护**
    - 对现有的 6 个 CV trace generator（yolov8n, resnet18/50, vit, qwen_vl_vit, sd_unet）添加 pytest，确保 trace 生成不崩溃、维度合法。
    - Acceptance: `PYTHONPATH=sim pytest sim/cv/tests/ -q` 全部通过。
    - Commit: `test(cv): add regression tests for all CV trace generators`

- [x] 18. **P4: 性能数据记录**
    - 在 signoff evidence 中添加 wall-time elapsed（per-gate 和 total），便于追踪软件栈路径的性能变化。
    - Acceptance: evidence JSON 中包含 `elapsed_sec` 字段。
    - Commit: `feat(signoff): record wall-time elapsed in e2e evidence`

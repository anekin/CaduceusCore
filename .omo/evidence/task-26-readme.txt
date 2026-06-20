=== Task 26: README.md Update Verification ===
Date: 2026-06-20

1. PATH AUDIT: No hardcoded user paths
   - /Users/: 0 occurrences
   - /home/zhengs: 0 occurrences
   - ~/npu: 0 occurrences
   - All paths use relative (e.g., sim/, docs/) or $HOME

2. REFERENCED FILE AUDIT:
   OK: sim/arc_model.py
   OK: sim/func_model.py
   OK: sim/golden_executor.py
   OK: sim/npu_sim.py
   OK: sim/design_space_explorer.py
   OK: sim/e2e_llamacpp.py
   OK: sim/gen_rtl_tests.py
   OK: sim/compare_rtl.py
   OK: sim/engine/
   OK: sim/models/
   OK: sim/config/
   OK: sim/tests/
   OK: ggml-npu/q4_dequant.py
   OK: ggml-npu/npu_server.py
   OK: ggml-npu/verify_hex.py
   OK: docs/NPU硬件详细架构设计v0.1.md
   OK: docs/NPU软件架构方案v0.2.md
   OK: docs/func_model_architecture.md
   OK: docs/verification_methodology.md
   OK: docs/NPU_Engines_Architecture_Guide.md
   OK: ../.omo/drafts/caduceuscore-cv-analysis.md

3. VERIFICATION STATUS CLAIMS (verified at runtime):
   - Smoke: 10/10 PASS (golden_executor.py smoke)
   - SFU Verify: 19/19 PASS (golden_executor.py sfu-verify)
   - pytest: 109/109 PASS (pytest sim/tests/)
   - FM status correctly stated as "仍在开发中" (not PASS)

4. SECTIONS (13 total):
   - 架构概览
   - 设计空间探索结论
   - 开发工作流 (NEW - three-stage diagram)
   - Func Model — 双重角色 (NEW)
   - 验证体系 (REWRITTEN - three layers with status)
   - CV 模型支持 (NEW)
   - 快速开始 (REWRITTEN - dependencies + model download + verification)
   - 项目结构 (UPDATED - paths, file list)
   - 软件栈方案
   - 设计理念 (UPDATED - references new sections)
   - 量化方案
   - Lessons Learned
   - License

5. CORRECTIONS APPLIED:
   - FM bit-exact PASS → "仍在开发中"
   - SFU/Vector 空桩 and trace path crash documented
   - ~/npu/ paths → CaduceusCore/ relative paths
   - Arc Model vs Func Model responsibilities distinguished
   - $readmemh golden reference mechanism explained
   - CV roadmap added (YOLOv8 + ResNet, im2col, hardware reuse)
   - Quick start: dependency install + model download + verification commands

# problems

## [2026-09-23] session start

## [2026-09-23] todo 3 — 遗留缺陷与过程债（待用户决定是否立案）

### 既有功能缺陷（pre-existing，非本计划引入；RED/GREEN/GREEN2 三次签名逐字节相同）
- **DEFECT-WRP-1 — store-out AXI drain 早于 `STATUS.DONE`**：`test_mxu_store_out_burst` 失败，`MISMATCH: 3180/4096`，首错行 11（写突发止于第 10 行）。与模块自身"poll DONE → 读结果"契约矛盾 → **firmware 可见**。
- **DEFECT-WRP-2 — accumulate 模式 `m_axi_wdata` 出现 X**：`test_mxu_accumulate_mode` 失败 `ValueError: Unresolvable bit 'x'`。

### 过程债（工具链可信度）
- **PROCESS-1** — `run_wrapper_mxu` 经 `soc-verification-run.sh` 调用报 `Error 127`：`sim/regression/Makefile:1313-1315` 缺 `cd $(REPO_ROOT) &&` 前缀（同族 sfu/vector target 同样形态）。绕过：直接 `bash scripts/wv_run_mxu.sh`。
- **PROCESS-2** — runner 判定 grep 假阳性：`scripts/wv_run_mxu.sh:116` 用 `grep -qE 'TEST.*PASS'`，会匹配**失败**运行的汇总行（`TESTS=1 PASS=0 FAIL=1`）→ 对 RED 运行也打印 "All 6 tests PASSED"。判定必须解析 per-test 行。
- **PROCESS-3** — tracked `build/evidence/wrap-mxu-regression.txt`（"5 PASS"）不可作为证据：与 cocotb 实际 per-test 行（4 PASS/2 FAIL）矛盾；HEAD 控制跑显示真实基线为 1 PASS/4 FAIL（`ApbMaster` 缺 `clk=` 崩溃）。
- 附注：`sim/tests/wrapper/test_mxu_wrapper.py` 的 3 处调用点已在本 todo 内（白名单内）补 `clk=dut.clk`（修复崩溃的语义中性改动，已披露）。

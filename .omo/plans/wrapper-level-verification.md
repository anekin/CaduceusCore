# wrapper-level-verification - Work Plan

## TL;DR (For humans)

**What you'll get:** 三个 SoC 引擎 wrapper（SFU/Vector/MXU）各自获得独立的 cocotb+VCS testbench，用 cocotbext-axi 的 `ApbMaster` 驱动 APB 寄存器（TB 顶层暴露 `apb_*` 前缀端口含 `apb_pstrb`）、`AxiRam` 做 AXI4 slave 端功能验证、自研 Verilog 行为 AXI4 slave（`axi_sparse_slave.v`，含未初始化 `reg` 数组）做 BUG-005 X-propagation 测试。验证 wrapper 层的 APB→MMIO 路径、AXI4 burst 几何、宽度转换器正确性、非对齐 padding 边界、以及多 op 连续 dispatch 时序。完成后 BUG-005（X-propagation）和 BUG-007（attn_weight dispatch）可在 wrapper 级独立复现或排除。

**Why this approach:** IP 级 testbench（319 SFU + 63 Vector 全 PASS）验证的是 `sfu_top`/`vector_top` 内部计算功能，从未实例化过 wrapper。wrapper 是 Phase 3 SoC 集成时新增的 APB+AXI4 胶水层，其宽度转换器（32↔512、4096↔512）、burst 拆包逻辑、START gating 时序只在 SoC 全链路中被间接覆盖，合成测试向量尺寸对齐不触发边界问题。`AxiRam`（Python 侧行为模型）对未初始化区域返回 0 而非 X，因此 BUG-005 的 X-propagation 测试需要一个 Verilog 侧行为 AXI4 slave 模型（`axi_sparse_slave.v`），其内部使用未初始化 `reg` 数组，Verilog 仿真自然产生 X。功能测试用 `AxiRam`（快速、sparse memory 后门直写），X-propagation 定向测试切换到 `axi_sparse_slave.v`。所有组件均无需 Ibex/Crossbar/DRAM 参与即可隔离根因。

**What it will NOT do:** 不修改任何 RTL 源码（wrapper 或引擎）；不修改固件；不修改 `sim/cocotb_bridge.py` 或 `sim/rtl_soc_runner.py`；不做 SoC 级集成测试；不测 `mxu_top.v` 内部功能（如 watchdog BUG-MXU-WDT-001）；不做 Arc Model/DSE 变更。

**Effort:** Medium
**Risk:** Low — 只新增 testbench/脚本/证据文件，不改产品代码
**Decisions to sanity-check:** (1) 用 cocotbext-axi `ApbMaster` 而非项目已有的自定义 `SimpleAPBMaster`；(2) 三个 wrapper 全覆盖（而非只测已知有 bug 的 SFU/Vector）；(3) 只验证不修 bug——若发现新 bug 则登记并延后到独立修复 plan。

Your next move: 等双 high-accuracy 审阅 OKAY 后 approve, 然后执行 `/start-work wrapper-level-verification`. Full execution detail follows below.

---

> TL;DR (machine): Medium / Low — 3 cocotb wrapper testbenches (SFU/Vector/MXU) with ApbMaster (apb_* prefix + pstrb) + AxiRam (functional) + axi_sparse_slave.v (X-prop), 8 todo + F1-F4.

## Scope
### Must have
- **脚本优先原则（沿用 Phase 9 约定）**: 工具调用、EDA 环境变量设置、SSH 执行封装为 `scripts/wv_*.sh` 脚本；todo 内只允许 `bash scripts/wv_<name>.sh [args]` 一个入口。脚本头部 `source "$(dirname "$0")/p9_lib/p9_sz0001.sh"` 复用 Phase 9 的 SSH+VCS env wrapper（已存在于 `scripts/p9_lib/p9_sz0001.sh`）。
- **所有验证在 sz0001（沿用 Phase 9 约定）**: VCS 编译+仿真经 `p9_ssh()` 在 sz0001 上执行；Python cocotb 测试也经 `p9_ssh()` 跑（sz0001 有 cocotbext-axi 安装环境）。
- 3 个新 Verilog testbench：`rtl/tb/tb_sfu_wrapper.v`、`rtl/tb/tb_vector_wrapper.v`、`rtl/tb/tb_mxu_wrapper.v`——每个只实例化一个 wrapper（wrapper 内已含 `apb_to_mmio`，TB **不**单独再实例化）；TB 顶层暴露 `apb_*` 前缀端口（`apb_psel/apb_penable/apb_pwrite/apb_paddr/apb_pwdata/apb_prdata/apb_pready/apb_pslverr/apb_pstrb`）并连线到 wrapper 的 bare APB 端口；`apb_pstrb` 接 0（wrapper 不处理 strobe）；AXI4 master 端信号作为 TB 顶层端口供 cocotb 驱动
- 1 个新 Verilog 行为 AXI4 slave 模型：`rtl/tb/axi_sparse_slave.v`——含未初始化 `reg` 数组做 memory，Verilog 仿真自然返回 X 读取未初始化地址；用于 BUG-005 X-propagation 定向测试（`AxiRam` Python 模型对未初始化区域返回 0 而非 X，无法复现 X-prop）
- 1 个 Python 公共库：`sim/tests/wrapper/wrapper_common.py`——封装 ApbMaster/AxiRam 创建、非对齐数据生成器、结果比对 helper
- 3 个 Python cocotb 测试模块：`sim/tests/wrapper/test_sfu_wrapper.py`、`test_vector_wrapper.py`、`test_mxu_wrapper.py`
- 1 个 VCS 编译 flist：`rtl/tb/wrapper.flist`
- BUG-005 定向测试：非对齐 burst padding（element count ≠ 128 的倍数）+ `axi_sparse_slave.v` 未初始化 `reg` 数组 X 传播（AxiRam 返回 0 非 X，故用 Verilog 侧行为模型）
- BUG-007 定向测试：连续多 op dispatch（consecutive CMD.START without full idle gap）
- Makefile targets：`run_wrapper_sfu`、`run_wrapper_vector`、`run_wrapper_mxu`、`run_wrapper_all`
- Evidence 文件：`build/evidence/wrap-*.txt`
- **Bug-tracking 强制（沿用 Phase 9 约定）**: 验证过程中发现任何新 wrapper bug，立即追加到 `docs/bugs/bugs-soc-rtl.md`（`BUG-RTL-SOC-WV-NNN` 编号），疑似 RTL bug 另产独立报告 `docs/bugs/BUG-WV-NNN-<slug>.md`

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No RTL changes to `sfu_soc_wrapper.v`、`vector_soc_wrapper.v`、`mxu_soc_wrapper.v` 或任何 `rtl/` 下的文件
- No firmware changes（`firmware/npu_firmware.c` 等）
- No changes to `sim/cocotb_bridge.py`、`sim/rtl_soc_runner.py`、`sim/spike_rtl_bridge.py`
- No SoC-level integration tests（不实例化 crossbar/DRAM/CPU）
- No MXU controller watchdog（BUG-MXU-WDT-001）测试——在 `mxu_top.v` 内部，非 wrapper 范围
- No Arc Model / DSE changes
- No inline `ssh`/`vcs`/`module load` 命令字面量在 todo 内（违反 SCRIPT-FIRST，F1 reject）
- No 在本机（非 sz0001）跑任何验证命令

## Verification strategy
> Zero human intervention - all verification is agent-executed, all on sz0001, all via scripts.
- 脚本目录: 复用已有 `scripts/p9_lib/p9_sz0001.sh`（SSH+VCS env wrapper）；新增 `scripts/wv_*.sh` 每个 todo 验证动作一个脚本
- Test decision: tests-after（先建 TB + 脚手架，再跑测试）+ cocotb + VCS inline comparison
- Evidence: `build/evidence/wrap-*.txt|jsonl`（所有证据写入 NFS 共享路径）
- Bug-tracking: 发现 bug 先 `bash scripts/wv_log_bug.sh --id BUG-RTL-SOC-WV-NNN ...` 追加到 `docs/bugs/bugs-soc-rtl.md` 再 commit
- Regression gate: 每个 wrapper 的测试全部 PASS 才标记 todo 完成

## Execution strategy
### Parallel execution waves
- Wave 0 (serial): T1 共用脚手架 + VCS flist + axi_sparse_slave.v + wrapper_common.py
- Wave 1 (parallel): T2 SFU wrapper 测试, T3 Vector wrapper 测试, T4 MXU wrapper 测试, T7 Makefile targets (提前建)
- Wave 2 (parallel): T5 BUG-005 定向测试, T6 BUG-007 定向测试
- Wave 3 (serial): T8 wrapper 级回归 + 文档 + closure
- Final (parallel after T8): F1-F4

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2,3,4,5,6,7,8 | — |
| 2 | 1 | 5, 7, 8 | 3, 4 |
| 3 | 1 | 5, 7, 8 | 2, 4 |
| 4 | 1 | 6, 7, 8 | 2, 3 |
| 5 | 2, 3 | 8 | 6 |
| 6 | 2, 4 | 8 | 5 |
| 7 | 2, 3, 4 | 8 | — |
| 8 | 2, 3, 4, 5, 6, 7 | F1-F4 | — |
| F1-F4 | 8 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 0 — Scaffolding (serial)

- [x] 1. 共用脚手架：VCS wrapper flist + 3 个 Verilog TB skeleton + axi_sparse_slave.v + wrapper_common.py 公共库 + wv_*.sh 脚本骨架
  What to do:
    0. 创建 `scripts/wv_bootstrap.sh`（唯一 bootstrap 脚本）：创建 `scripts/wv_*.sh` 脚本骨架（wv_compile.sh, wv_run_sfu.sh, wv_run_vector.sh, wv_run_mxu.sh, wv_run_bug005.sh, wv_run_bug007.sh, wv_log_bug.sh, wv_regression.sh, wv_f1_audit.sh, wv_f2_scope_gate.sh, wv_f3_qa.sh, wv_f4_scope.sh），全部 `chmod +x`，脚本头部 `source "$(dirname "$0")/p9_lib/p9_sz0001.sh"` 复用 Phase 9 SSH wrapper。
    1. 创建 `rtl/tb/wrapper.flist`：列出 wrapper testbench 编译所需的全部文件（wrapper 本体 + apb_to_mmio.v + 引擎 RTL + axi_sparse_slave.v）。VCS 编译参数（在 `wv_compile.sh` 内部）：`+define+COCOTB_SIM=1 +vpi -P sim/regression/pli.tab -load $(cocotb-config --lib-name-path vpi vcs)`。
    2. 创建 `rtl/tb/tb_sfu_wrapper.v` skeleton：
       - clk/rst 生成器（100MHz，`always #5 clk=~clk`，`initial rst_n=0; #20 rst_n=1`）
       - TB 顶层端口：`apb_psel/apb_penable/apb_pwrite/apb_paddr[11:0]/apb_pwdata[31:0]/apb_prdata[31:0]/apb_pready/apb_pslverr/apb_pstrb[3:0]`（`apb_pstrb` 接 `4'b0`，wrapper 不处理 strobe 但 cocotbext-axi `ApbBus` 要求此信号）
       - AXI4 master 端作为 TB 顶层端口 `m_axi_*`（供 cocotb `AxiRam` 或 `AxiBus` 驱动）
       - wrapper 实例化：TB 的 `apb_*` 信号连接到 wrapper 的 bare `psel/penable/...` 端口；wrapper 内已含 `apb_to_mmio`，TB **不**再单独实例化
    3. 创建 `rtl/tb/tb_vector_wrapper.v` skeleton：同上结构，实例化 `vector_soc_wrapper`。
    4. 创建 `rtl/tb/tb_mxu_wrapper.v` skeleton：同上，实例化 `mxu_soc_wrapper`，含 debug 端口输出。
    5. 创建 `rtl/tb/axi_sparse_slave.v`：Verilog 行为 AXI4 slave 模型，512-bit 数据宽度。内部使用 `reg [511:0] mem [0:DEPTH-1]` 做 memory，**不**加 `initial` 清零——Verilog 仿真中未初始化的 `reg` 数组自然为 X。接收 AXI4 AR burst 返回 `mem[addr]`（未初始化地址返回 X）；接收 AW+W burst 写入 `mem[addr]`。支持 INCR burst。参数化 `DEPTH`（默认 4096，2MB@512-bit）。此模型用于 T5 BUG-005 X-propagation 定向测试；功能性测试（T2/T3/T4）用 cocotbext-axi `AxiRam`（Python 侧 sparse memory，后门直写更方便）。
    6. 创建 `sim/tests/wrapper/wrapper_common.py`：
       - `create_apb_master(dut)`: `ApbBus.from_prefix(dut, "apb")` + `ApbMaster(bus, dut.clk, dut.rst_n, reset_active_level=False)` — **必须传 `reset_active_level=False`**（wrapper 用 `rst_n` 低有效，ApbMaster 默认 active-high）
       - `create_axi_ram(dut, size=2**20)`: `AxiBus.from_prefix(dut, "m_axi")` + `AxiRam(bus, dut.clk, dut.rst_n, reset_active_level=False, size=size)` — 同理
       - `write_reg(apb, base, offset, value)`: APB 寄存器写 helper
       - `read_reg(apb, base, offset)`: APB 寄存器读 helper
       - `wait_done(apb, base, timeout_cycles=100000)`: 轮询 STATUS.DONE（timeout 设大：MXU accumulate 可能跑数万 cycle）
       - `gen_nonaligned_data(n_elements)`: 生成非对齐 element count（n % 128 ≠ 0）
       - `check_no_x(data)`: 检查 cocotb 信号值不含 X（用 `BinaryValue.is_resolvable` 或 `bin_str` 中不含 'x'）
    7. `bash scripts/wv_bootstrap.sh` 执行 bootstrap。
    8. `bash scripts/wv_compile.sh`（在 sz0001 上用 VCS + cocotb VPI 编译 3 个 wrapper testbench + axi_sparse_slave，校验 elaboration 通过；脚本内部 echo `VCS_EXIT_CODE=$?` 到 `build/evidence/wv-compile.log`）。
  Must NOT do:
    - 不修改任何 RTL 源码或已有文件
    - 不修改 firmware 或现有 Python 文件
    - TB **不**单独实例化 `apb_to_mmio`（已在 wrapper 内部）
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2,3,4,5,6,7,8 | Can parallelize with: none
  References:
    - `rtl/wrapper/sfu_soc_wrapper.v:22-80`（wrapper 端口定义，注意 APB 端口是 bare `psel` 等无 `apb_` 前缀）
    - `rtl/wrapper/vector_soc_wrapper.v:43-114`（端口 + 内部 buffer）
    - `rtl/wrapper/mxu_soc_wrapper.v:30-100`（端口 + debug）
    - `rtl/wrapper/apb_to_mmio.v`（APB→MMIO 桥，**已内嵌在 wrapper 中**，TB 不单独实例化）
    - `sim/cocotb_bridge.py:68`（`from cocotbext.axi import AxiBus, AxiMaster, AxiRam`）
    - `sim/cocotb_bridge.py:452-457`（`AxiMaster(..., reset_active_level=False)` — **必须传此参数**）
    - `sim/spike_rtl_bridge.py:49-105`（SimpleAPBMaster 参考实现——通过信号 prefix 驱动 APB）
    - `rtl/tb/tb_sfu.v:1-30`（IP 级 TB 参考结构）
    - `scripts/p9_lib/p9_sz0001.sh`（SSH wrapper，复用）
    - `sim/regression/Makefile:96-109`（cocotb VPI 编译参数参考：`+define+COCOTB_SIM=1 +vpi -P pli.tab -load $(COCOTB_VPI_LIB)`）
    - cocotbext-axi docs: `ApbBus._signals` 含 `pstrb`（必需，非 optional）；`ApbMaster(ApbBus.from_prefix(dut, "apb"), clk, rst, reset_active_level=False)`；`AxiRam` 默认 `reset_active_level=True`，wrapper 用 `rst_n` 低有效时必须改 `False`
    - cocotbext-axi `AxiRam` sparse memory 对未初始化区域返回 0（非 X）——故需 `axi_sparse_slave.v` 做 X-propagation 测试
  Acceptance criteria (agent-executable):
    - `test -x scripts/wv_bootstrap.sh -a -x scripts/wv_compile.sh`
    - `test -f rtl/tb/wrapper.flist`
    - `test -f rtl/tb/tb_sfu_wrapper.v -a -f rtl/tb/tb_vector_wrapper.v -a -f rtl/tb/tb_mxu_wrapper.v`
    - `test -f rtl/tb/axi_sparse_slave.v`
    - `grep -q 'axi_sparse_slave' rtl/tb/wrapper.flist`（flist 含新 slave 模型）
    - `test -f sim/tests/wrapper/wrapper_common.py`
    - `python3 -c "import ast; ast.parse(open('sim/tests/wrapper/wrapper_common.py').read()); print('AST OK')"`
    - `grep -q 'reset_active_level=False' sim/tests/wrapper/wrapper_common.py`（reset 极性已正确设置）
    - `grep -q 'ApbMaster\|ApbBus' sim/tests/wrapper/wrapper_common.py`
    - `grep -q 'AxiRam\|AxiBus' sim/tests/wrapper/wrapper_common.py`
    - `grep -q 'apb_pstrb' rtl/tb/tb_sfu_wrapper.v`（TB 含 pstrb 信号）
    - `bash scripts/wv_compile.sh` 退出码 0 且 `grep -q 'VCS_EXIT_CODE=0' build/evidence/wv-compile.log`
  QA scenarios:
    - Happy: 3 个 TB skeleton + axi_sparse_slave.v elaboration 通过，AST OK，reset_active_level=False 已设
    - Failure: VCS elaboration 报端口连接错误 → 检查 wrapper 端口宽度匹配、apb_pstrb 是否已暴露
    - Evidence: `build/evidence/wv-compile.log`
  Commit: Y | `chore(wrapper-vv): scaffold 3 wrapper TBs + axi_sparse_slave + common lib + scripts`

### Wave 1 — Per-wrapper functional tests (parallel)

- [x] 2. SFU wrapper 功能测试：APB regmap + 32↔512 width converter + line buffer prefetch
  What to do:
    1. 创建 `sim/tests/wrapper/test_sfu_wrapper.py`，包含 5 个 cocotb test case：`test_apb_regmap_rw`（写读 regmap 0x00-0x1C）、`test_sfu_softmax_normal`（256 元素 FP16 softmax）、`test_sfu_gelu_normal`（GELU op）、`test_sfu_width_converter_32to512`（16×32-bit→512-bit 写打包验证）、`test_sfu_line_buffer_prefetch`（非缓存行对齐 I_ADDR 触发 prefetch FSM 验证）。
    2. `bash scripts/wv_run_sfu.sh`（在 sz0001 编译 + 跑 SFU wrapper cocotb tests）。
  Must NOT do:
    - 不修改 `sfu_soc_wrapper.v` 或 `sfu_top.v` 或任何 RTL 文件
    - 不依赖 crossbar 或 DRAM model
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5, 7 | Can parallelize with: 3, 4
  References:
    - `rtl/wrapper/sfu_soc_wrapper.v:22-80`（端口）、`:141-143`（rd_state FSM）、`:407-409`（wr_state FSM）、`:364`（WR_FIFO_DEPTH）
    - `rtl/sfu/README.md:124-158`（sfu_top MMIO regmap）
    - `rtl/sfu/README.md:32-46`（softmax 操作语义）
    - `sim/tests/wrapper/wrapper_common.py`（T1 产物）
    - `scripts/gen_sfu_vectors.py`（golden 数据参考）
    - `scripts/compare_sfu.py`（FP16 容差：abs_tol=2e-3, rel_tol=1e-2）
  Acceptance criteria (agent-executable):
    - `test -s sim/tests/wrapper/test_sfu_wrapper.py`
    - `python3 -c "import ast; ast.parse(open('sim/tests/wrapper/test_sfu_wrapper.py').read()); print('AST OK')"`
    - `grep -q 'test_apb_regmap_rw' sim/tests/wrapper/test_sfu_wrapper.py`
    - `grep -q 'test_sfu_softmax_normal' sim/tests/wrapper/test_sfu_wrapper.py`
    - `grep -q 'test_sfu_width_converter' sim/tests/wrapper/test_sfu_wrapper.py`
    - `bash scripts/wv_run_sfu.sh` 退出码 0
    - `grep -q 'PASS' build/evidence/wrap-sfu-regression.txt`
    - `! grep -q 'FAIL\|ERROR' build/evidence/wrap-sfu-regression.txt`
  QA scenarios:
    - Happy: 5 个 test case 全 PASS
    - Failure: line buffer prefetch 在非对齐 I_ADDR 读到 X → 记 BUG-RTL-SOC-WV-001
    - Evidence: `build/evidence/wrap-sfu-regression.txt`
  Commit: Y | `test(wrapper-vv): SFU wrapper functional tests — APB + width converter + prefetch`

- [x] 3. Vector wrapper 功能测试：APB regmap + 4096↔512 chunk burst + WRP_CMD 序列
  What to do:
    1. 创建 `sim/tests/wrapper/test_vector_wrapper.py`，包含 5 个 test case：`test_apb_native_rw`（regmap 0x00-0x1C）、`test_apb_wrapper_rw`（wrapper MMIO 0x30-0x44）、`test_vector_add_normal`（128 INT32 ADD）、`test_vector_chunk_burst_8beat`（验证 arlen=7/awlen=7/arsize=6 burst 几何）、`test_vector_conv_type_convert`（INT32→FP16 CONV）。
    2. `bash scripts/wv_run_vector.sh`（在 sz0001 编译 + 跑 Vector wrapper cocotb tests）。
  Must NOT do:
    - 不修改 `vector_soc_wrapper.v` 或 `vector_top.v`
    - 不依赖 crossbar 或 DRAM model
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 5, 7 | Can parallelize with: 2, 4
  References:
    - `rtl/wrapper/vector_soc_wrapper.v:43-114`（端口+参数）、`:218-233`（seq_state FSM）、`:421-424`（arlen=7, awlen=7）
    - `rtl/vector/README.md:107-123`（vector_top MMIO regmap + processing model）
    - `sim/tests/wrapper/wrapper_common.py`（T1 产物）
    - `scripts/gen_vector_vectors.py`（golden 参考）
  Acceptance criteria (agent-executable):
    - `test -s sim/tests/wrapper/test_vector_wrapper.py`
    - `python3 -c "import ast; ast.parse(open('sim/tests/wrapper/test_vector_wrapper.py').read()); print('AST OK')"`
    - `grep -q 'test_apb_native_rw' sim/tests/wrapper/test_vector_wrapper.py`
    - `grep -q 'test_vector_add_normal' sim/tests/wrapper/test_vector_wrapper.py`
    - `grep -q 'test_vector_chunk_burst' sim/tests/wrapper/test_vector_wrapper.py`
    - `bash scripts/wv_run_vector.sh` 退出码 0
    - `grep -q 'PASS' build/evidence/wrap-vec-regression.txt`
    - `! grep -q 'FAIL\|ERROR' build/evidence/wrap-vec-regression.txt`
  QA scenarios:
    - Happy: 5 个 test case 全 PASS，8-beat burst 几何正确
    - Failure: chunk burst 在非整数倍 element count 时 wstrb padding 区有 X → 记 BUG-RTL-SOC-WV-002
    - Evidence: `build/evidence/wrap-vec-regression.txt`
  Commit: Y | `test(wrapper-vv): Vector wrapper functional tests — APB + chunk burst + CONV`

- [x] 4. MXU wrapper 功能测试：APB regmap + preload/store-out + broadcast + accumulate mode
  What to do:
    1. 创建 `sim/tests/wrapper/test_mxu_wrapper.py`，包含 5 个 test case：`test_apb_regmap_rw`（native MMIO 0x00-0x28 + wrapper MMIO 0x30-0x48）、`test_mxu_preload_single_tile`（weight 2048B + act 4096B preload，PL FSM 验证）、`test_mxu_single_tile_compute`（preload + START + store-out + golden 比对）、`test_mxu_store_out_burst`（2048-bit→4×512-bit burst 几何）、`test_mxu_accumulate_mode`（K=128 跨 tile 累加）。
    2. `bash scripts/wv_run_mxu.sh`（在 sz0001 编译 + 跑 MXU wrapper cocotb tests）。
  Must NOT do:
    - 不修改 `mxu_soc_wrapper.v` 或 `mxu_top.v` 或任何 RTL 文件
    - 不测 watchdog（BUG-MXU-WDT-001）
    - 不依赖 crossbar 或 DRAM model
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6, 7 | Can parallelize with: 2, 3
  References:
    - `rtl/wrapper/mxu_soc_wrapper.v:30-100`（端口+debug）、`:289-362`（pl_state preload FSM）、`:477-631`（so_state store-out FSM）
    - `scripts/gen_mxu_vectors.py`（golden 参考）
    - `sim/compare_rtl.py`（INT32 bit-exact 比较）
    - Phase 9 `ctrl_acc_mode`（`rtl/mxu/controller.v`、`rtl/mxu/mmio_if.v`——只读参考）
  Acceptance criteria (agent-executable):
    - `test -s sim/tests/wrapper/test_mxu_wrapper.py`
    - `python3 -c "import ast; ast.parse(open('sim/tests/wrapper/test_mxu_wrapper.py').read()); print('AST OK')"`
    - `grep -q 'test_apb_regmap_rw' sim/tests/wrapper/test_mxu_wrapper.py`
    - `grep -q 'test_mxu_preload' sim/tests/wrapper/test_mxu_wrapper.py`
    - `grep -q 'test_mxu_accumulate_mode' sim/tests/wrapper/test_mxu_wrapper.py`
    - `bash scripts/wv_run_mxu.sh` 退出码 0
    - `grep -q 'PASS' build/evidence/wrap-mxu-regression.txt`
    - `! grep -q 'FAIL\|ERROR' build/evidence/wrap-mxu-regression.txt`
  QA scenarios:
    - Happy: 5 个 test case 全 PASS，preload + store-out + accumulate 正确
    - Failure: accumulate mode K>64 结果不一致 → 记 BUG-RTL-SOC-WV-003
    - Evidence: `build/evidence/wrap-mxu-regression.txt`
  Commit: Y | `test(wrapper-vv): MXU wrapper functional tests — preload + store-out + accumulate`

### Wave 2 — Bug-directed tests (parallel)

- [x] 5. BUG-005 定向测试：非对齐 burst padding + axi_sparse_slave.v 未初始化 reg 数组 X-propagation
  What to do:
    1. 在 `test_sfu_wrapper.py` 追加 `test_bug005_sfu_nonaligned_xprop`：
       a. 本 test case 使用 `axi_sparse_slave.v` 做 AXI4 slave（不用 AxiRam——AxiRam Python 侧返回 0 非 X，无法复现 X-prop）
       b. 通过 cocotb AXI4 master 驱动 `axi_sparse_slave.v` 的 `m_axi_*` 端口，用 APB master 向 wrapper 写 I_ADDR=0, DIM=25, CMD.START softmax
       c. `axi_sparse_slave.v` 的 `reg` 数组未初始化 → 读 addr 0-99 为 X（Verilog 自然 X），但通过 cocotb 先用 AXI4 write burst 向 addr 0-99 写入有效 FP16 数据（仅写 100 字节，非 64-byte 对齐）
       d. wrapper line buffer prefetch 会读 64-byte cache line → addr 100-127 仍为 X → 检查 SFU 输出是否含 X 值
       e. 如果输出含 X → BUG-005 SFU 侧复现成功；如果无 X → SFU wrapper 在此 case 下安全
    2. 在 `test_vector_wrapper.py` 追加 `test_bug005_vector_nonaligned_wstrb`：
       a. 同样使用 `axi_sparse_slave.v` 做 AXI4 slave
       b. 通过 AXI4 write burst 向 addr 0-399 写入 100 个 INT32 元素（400B，非 512 对齐），addr 400-511 仍为 X
       c. WRP_LEN=100，WRP_CMD LOAD_A，APB CMD.START ADD op，WRP_CMD STORE_O
       d. 验证 wstrb masking（`vector_soc_wrapper.v:446-474`）是否有效：如果有效，store-out 不写 addr 400-511；如果无效，X 传播到输出
    3. `bash scripts/wv_run_bug005.sh`（跑两个定向测试，结果写入 `build/evidence/wrap-bug005-result.txt`）。
    4. 注意：此 test case 的编译需要单独 TB 实例化 `axi_sparse_slave.v`（而非 `AxiRam`）——可在 `wv_run_bug005.sh` 中用 `+define+USE_SPARSE_SLAVE=1` 条件编译切换，或使用单独的 TB top。
  Must NOT do:
    - 不修改任何 RTL 文件
    - 发现 bug 不在此修复——登记后延后
  Parallelization: Wave 2 | Blocked by: 2, 3 | Blocks: 8 | Can parallelize with: 6
  References:
    - `docs/bugs/bugs-soc-rtl.md:262-309`（BUG-005 完整描述 + workaround 失败）
    - `docs/bugs/bugs-soc-rtl.md:181-213`（BUG-005 first entry — vector fix status=Fixed）
    - `rtl/wrapper/vector_soc_wrapper.v:446-474`（wstrb masking 修复——只读）
    - `rtl/wrapper/sfu_soc_wrapper.v:141-143,282-340`（read prefetch FSM——只读）
    - `rtl/tb/axi_sparse_slave.v`（T1 产物——Verilog 行为 AXI4 slave，未初始化 reg 返回 X）
    - `sim/tests/wrapper/wrapper_common.py`（`gen_nonaligned_data`, `check_no_x`）
  Acceptance criteria (agent-executable):
    - `grep -q 'test_bug005_sfu_nonaligned' sim/tests/wrapper/test_sfu_wrapper.py`
    - `grep -q 'test_bug005_vector_nonaligned' sim/tests/wrapper/test_vector_wrapper.py`
    - `bash scripts/wv_run_bug005.sh` 退出码 0
    - `grep -qE 'SFU.*(PASS|FAIL|X_PROP)' build/evidence/wrap-bug005-result.txt`
    - `grep -qE 'Vector.*(PASS|FAIL|X_PROP)' build/evidence/wrap-bug005-result.txt`
  QA scenarios:
    - Happy: Vector wstrb masking 有效→无 X；SFU prefetch 安全→无 X
    - Failure (Vector): wstrb 遗漏→X 传播→记 BUG-RTL-SOC-WV-002 open
    - Failure (SFU): prefetch 读 X→传播→记 BUG-RTL-SOC-WV-001 open
    - Evidence: `build/evidence/wrap-bug005-result.txt`
  Commit: Y | `test(wrapper-vv): BUG-005 directed — non-aligned burst padding X-propagation`

- [x] 6. BUG-007 定向测试：连续多 op dispatch（consecutive CMD.START without full idle gap）
  What to do:
    1. 在 `test_mxu_wrapper.py` 追加 `test_bug007_consecutive_dispatch`：连续发 3 次 CMD.START（不等 DONE，间隔 0/1/5 cycle），检查每次 STATUS.BUSY assertion 和 START 是否被 swallow。
    2. 在 `test_sfu_wrapper.py` 追加 `test_bug007_sfu_start_hold`：连续发 2 次 CMD.START（softmax→GELU，0 cycle 间隔），检查 `start_hold` 阻塞和 `pready` stall。
    3. `bash scripts/wv_run_bug007.sh`（结果写入 `build/evidence/wrap-bug007-result.txt`）。
  Must NOT do:
    - 不修改任何 RTL 文件或固件
    - 如果根因在固件 ring buffer 而非 wrapper，标注 "firmware-layer issue"
  Parallelization: Wave 2 | Blocked by: 2, 4 | Blocks: 8 | Can parallelize with: 5
  References:
    - `docs/bugs/bugs-soc-rtl.md:334-370`（BUG-007 三个假设）
    - `docs/bugs/bugs-soc-rtl.md:215-256`（BUG-006 start_hold race 已 Fixed）
    - `rtl/wrapper/sfu_soc_wrapper.v:216`（POST_START_STALL_CYCLES=2）
  Acceptance criteria (agent-executable):
    - `grep -q 'test_bug007_consecutive' sim/tests/wrapper/test_mxu_wrapper.py`
    - `grep -q 'test_bug007_sfu_start_hold' sim/tests/wrapper/test_sfu_wrapper.py`
    - `bash scripts/wv_run_bug007.sh` 退出码 0
    - `grep -qE 'MXU.*(PASS|FAIL)' build/evidence/wrap-bug007-result.txt`
    - `grep -qE 'SFU.*(PASS|FAIL)' build/evidence/wrap-bug007-result.txt`
  QA scenarios:
    - Happy (MXU): 3 次 START 全接收，无 swallow → wrapper 级无 BUG-007
    - Failure (MXU): START 被 swallow → 记 BUG-RTL-SOC-WV-004
    - Happy (SFU): start_hold 阻塞正确
    - Failure (SFU): start_hold 未阻塞 → 记 BUG-RTL-SOC-WV-005
    - Evidence: `build/evidence/wrap-bug007-result.txt`
  Commit: Y | `test(wrapper-vv): BUG-007 directed — consecutive dispatch + START gating`

### Wave 1b — Makefile targets (parallel with Wave 1)

- [x] 7. Makefile targets：`run_wrapper_sfu`/`run_wrapper_vector`/`run_wrapper_mxu`/`run_wrapper_all`
  What to do:
    1. 在 `sim/regression/Makefile` 追加 4 个新 targets：`run_wrapper_sfu`（调 `bash scripts/wv_run_sfu.sh`）、`run_wrapper_vector`、`run_wrapper_mxu`、`run_wrapper_all`（依赖前 3 个）。
    2. targets 遵循现有 compile→run→grep PASS 模式（参见 `sim/regression/Makefile` 中其他 cocotb target 的写法）。
  Must NOT do:
    - 不修改已有 Makefile targets
    - 不在此 todo 跑任何回归——只建 targets
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 8 | Can parallelize with: 2, 3, 4
  References:
    - `sim/regression/Makefile`（现有 target 模式，参见 `run_qwen_e2e` 等 cocotb target）
  Acceptance criteria (agent-executable):
    - `grep -q 'run_wrapper_sfu' sim/regression/Makefile`
    - `grep -q 'run_wrapper_vector' sim/regression/Makefile`
    - `grep -q 'run_wrapper_mxu' sim/regression/Makefile`
    - `grep -q 'run_wrapper_all' sim/regression/Makefile`
  QA scenarios:
    - Happy: 4 个 target 均已追加，`make -n run_wrapper_all` dry-run 不报错
    - Failure: Makefile 语法错误 → 修正后重试
    - Evidence: `sim/regression/Makefile` diff
  Commit: Y | `chore(wrapper-vv): add Makefile targets for wrapper regression`

### Wave 3 — Regression + closure (serial)

- [x] 8. Wrapper 级回归 + 文档 + closure
  What to do:
    1. 创建 `scripts/wv_regression.sh`：在 sz0001 上跑全量 wrapper 回归（SFU + Vector + MXU + BUG-005 + BUG-007），汇总到 `build/evidence/wrap-regression-summary.txt`。
    2. `bash scripts/wv_regression.sh`。
    3. 在 `docs/issues_found.md` 追加 `## Wrapper-Level Verification Results` 段：各 wrapper test case PASS/FAIL 统计、BUG-005 复现/排除结论、BUG-007 复现/排除结论、新发现 bug 清单。
    4. 生成 `build/evidence/wv-closure.txt`：列 PASS / NOT RESOLVED / 新 bug / forward actions。
  Must NOT do:
    - 不修改已有 Makefile targets
    - 不擅自把 FAIL 标 PASS
  Parallelization: Wave 3 | Blocked by: 2,3,4,5,6,7 | Blocks: F1-F4 | Can parallelize with: none
  References:
    - `sim/regression/Makefile`（T7 产物——`run_wrapper_all` target）
    - `scripts/wv_run_*.sh`（T2-T6 产物）
    - `docs/issues_found.md`（Phase 9 段参考格式）
  Acceptance criteria (agent-executable):
    - `bash scripts/wv_regression.sh` 退出码 0
    - `grep -qE 'SFU.*(PASS|FAIL)' build/evidence/wrap-regression-summary.txt`
    - `grep -qE 'Vector.*(PASS|FAIL)' build/evidence/wrap-regression-summary.txt`
    - `grep -qE 'MXU.*(PASS|FAIL)' build/evidence/wrap-regression-summary.txt`
    - `grep -q 'BUG-005' build/evidence/wrap-regression-summary.txt`
    - `grep -q 'BUG-007' build/evidence/wrap-regression-summary.txt`
    - `grep -q 'Wrapper-Level Verification Results' docs/issues_found.md`
    - `grep -qE 'PASS|FAIL|forward' build/evidence/wv-closure.txt`
  QA scenarios:
    - Happy: 全回归 PASS，closure 生成
    - Failure: 某 test FAIL → closure 标 NOT RESOLVED + forward，不阻塞 plan 完成
    - Evidence: `build/evidence/wrap-regression-summary.txt`, `build/evidence/wv-closure.txt`
  Commit: Y | `chore(wrapper-vv): wrapper regression + docs + closure`

## Final verification wave
> Runs in parallel after ALL todos (T1-T8 全 `[x]`). ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  What to do: `bash scripts/wv_f1_audit.sh`：检查所有 todo checkbox `[x]`，AC grep/test，输出 `build/evidence/wv-f1-audit.log`。
  Acceptance criteria (agent-executable):
    - `grep -q 'F1-AUDIT-PASS' build/evidence/wv-f1-audit.log`
    - `! grep -q 'FAIL:' build/evidence/wv-f1-audit.log`
  QA scenarios:
    - Happy: 全 todo [x]，所有 AC grep PASS
    - Failure: 任一 AC FAIL → 写 `wv-f1-fail-summary.txt`
    - Evidence: `build/evidence/wv-f1-audit.log`
  Commit: N

- [x] F2. Code quality review: 不修改已有 RTL/firmware/cocotb_bridge
  What to do: `bash scripts/wv_f2_scope_gate.sh`：检查 `git diff --name-only` 只含白名单新增文件（`rtl/tb/tb_*_wrapper.v`、`rtl/tb/axi_sparse_slave.v`、`rtl/tb/wrapper.flist`、`sim/tests/wrapper/*.py`、`scripts/wv_*.sh`、`build/evidence/wrap-*`、`build/evidence/wv-*`、`docs/issues_found.md`、`sim/regression/Makefile`）；AST OK；bridge 未动。
  Acceptance criteria (agent-executable):
    - `grep -q 'BRIDGE_UNCHANGED=1' build/evidence/wv-f2-code-quality.txt`
    - `grep -q 'SCOPE_CREEP=0' build/evidence/wv-f2-code-quality.txt`
    - `grep -q 'AST_OK=1' build/evidence/wv-f2-ast.txt`
  QA scenarios:
    - Happy: 改动只在白名单内，bridge 未动，AST OK
    - Failure: 超出白名单 → 写 `wv-f2-scope-creep.txt`
    - Evidence: `build/evidence/wv-f2-code-quality.txt`, `build/evidence/wv-f2-ast.txt`
  Commit: N

- [x] F3. Real manual QA: BUG-005/007 结论 + 回归 PASS
  What to do: `bash scripts/wv_f3_qa.sh`：检查 BUG-005 + BUG-007 结果 + 回归汇总。
  Acceptance criteria (agent-executable):
    - `grep -q 'BUG005_OK=1' build/evidence/wv-f3-checklist.txt`
    - `grep -q 'BUG007_OK=1' build/evidence/wv-f3-checklist.txt`
    - `grep -q 'REGRESSION_PASS=1' build/evidence/wv-f3-checklist.txt`
  QA scenarios:
    - Happy: BUG-005 和 BUG-007 有明确结论，回归汇总 PASS
    - Failure: 结论缺失或回归 FAIL → 写 `wv-f3-fail.txt`
    - Evidence: `build/evidence/wv-f3-checklist.txt`
  Commit: N

- [x] F4. Scope fidelity: 不改 RTL/firmware/bridge、不用 crossbar/DRAM
  What to do: `bash scripts/wv_f4_scope.sh`：检查无 RTL/firmware/cocotb_bridge 改动；testbench 无 crossbar/DRAM 实例化。
  Acceptance criteria (agent-executable):
    - `grep -q 'RTL_UNCHANGED=1' build/evidence/wv-f4-gate.txt`
    - `grep -q 'FIRMWARE_UNCHANGED=1' build/evidence/wv-f4-gate.txt`
    - `grep -q 'BRIDGE_UNCHANGED=1' build/evidence/wv-f4-gate.txt`
    - `grep -q 'NO_SOC_INSTANTIATION=1' build/evidence/wv-f4-gate.txt`
  QA scenarios:
    - Happy: 全 guardrail 通过
    - Failure: 任一 guardrail 破 → 写 `wv-f4-violation.txt`，HALT
    - Evidence: `build/evidence/wv-f4-gate.txt`
  Commit: N

## Commit strategy
- 每个 todo 完成立即 commit; 格式 `type(scope): summary`
- 类型: `chore` (T1, T7, T8), `test` (T2-T6)
- F1-F4 不 commit (审计)
- Bug-tracking: 发现 bug 先 `bash scripts/wv_log_bug.sh` 追加再 commit

## Success criteria

| 指标 | 阈值 |
|:---|:---:|
| 共用脚手架 | `scripts/wv_*.sh` + `rtl/tb/wrapper.flist` + `sim/tests/wrapper/wrapper_common.py` + `axi_sparse_slave.v` |
| 3 个 wrapper TB | elaboration 通过 |
| reset_active_level | `wrapper_common.py` 含 `reset_active_level=False` |
| apb_pstrb | TB skeleton 含 `apb_pstrb` 信号 |
| SFU wrapper 功能 | 5 test case PASS |
| Vector wrapper 功能 | 5 test case PASS |
| MXU wrapper 功能 | 5 test case PASS |
| BUG-005 复现/排除 | `wrap-bug005-result.txt` 有 SFU + Vector 结论 |
| BUG-007 复现/排除 | `wrap-bug007-result.txt` 有 MXU + SFU 结论 |
| Makefile targets | `run_wrapper_sfu` + `run_wrapper_vector` + `run_wrapper_mxu` + `run_wrapper_all` |
| Bug-tracking | 新 bug 登记 `docs/bugs/bugs-soc-rtl.md` |
| RTL/firmware/bridge 不动 | F4 gate 全 =1 |
| F1-F4 完整结构 | What-to-do / Acceptance / QA / Commit-N |

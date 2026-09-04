# sz0001-fm-env-setup - Work Plan

## TL;DR (For humans)
**What you'll get:** 在 sz0001 上建一个专用的 Func Model pytest 环境（独立 venv，离线装齐 pytest≥9/onnx/onnxruntime/flatbuffers 等），配好 python3 通路，然后重跑一次基线——目标是消除上一轮 113 failed/21 errors 里的环境性失败，让"全部验证在 sz0001"这条政策对 FM pytest 真正可执行。
**Why this approach:** sz0001 断网（DNS/出站全封），不能 pip 在线装；但 sz0002 已有完整 FM 依赖环境，可以作为 wheel 源头（若 sz0002 也断网则 STOP 上报）。venv 建在用户可写的 ~/venvs 下，不动 /NAS/Tools 的任何 root 环境。
**What it will NOT do:** 不装/不改任何 root 环境；不重建 spike/llama（glibc 2.17 重建属后续独立计划）；不修 8F 引擎漂移等既有 bug；不 push。

> TL;DR (machine): Medium | Medium | sz0001 离线 FM-pytest venv 搭建 + 基线重跑取证；wheel 源=sz0002（或 STOP）；5 个 cocotb pytest 文件经 --ignore 排除（归 VCS 流程）；spike/llama 测试暂留 sz0002（open item）。

## Scope
### Must have
1. **P0 快速确认**（用户已给出结论，只需实证落档）：sz0002 `curl -sI https://pypi.org/simple/` 通 + `pip download` 快测成功；NFS 共享 marker 测试（sz0002 建 `~/venvs/wheels/.probe-*`，sz0001 可见）；sz0002 `python3 -m pip list` 抓已装包版本；`ls ~/.cache/pip` 缓存盘点。落档 `.omo/evidence/sz0001-fmenv-p0.txt`（判定行 `FMENV-P0: network=yes nfs=shared wheels=<n-cached>`）。
2. **wheel 集齐**（T1）：sz0002（网络已证 yes）上 `pip download`（编译包走 `--platform manylinux2014_x86_64 --implementation cp --python-version 312 --abi cp312 --only-binary=:all:`，纯 python 包直接下载 py3-none-any）目标集（pytest>=9、pytest-asyncio、pytest-timeout、pydantic>=2、numpy>=2.2、flatbuffers==25.2.10、onnx>=1.22、protobuf>=3.20.2,<6、pyyaml、typing_extensions、packaging、pluggy、iniconfig、exceptiongroup、anyio 等传递依赖）→ 落到 **`<repo>/build/wheels`（gitignore 覆盖、NFS 两机可见；P0 实测 `~/venvs` 不跨机共享，故不用家目录）**。**onnxruntime==1.23.0 明确排除**（glibc 硬阻塞：PyPI 无 manylinux2014 wheel，2_27/2_28 版在 sz0001 glibc 2.17 不可加载——实测落档；其依赖测试文件留 sz0002，同 D2 模式）。
3. **venv 搭建**（T2）：sz0001 上用 base 3.12.4 `python -m venv ~/venvs/fmpytest`；`pip install --no-index --find-links ~/venvs/wheels -r <固定版本清单>`；冒烟：`pytest --version`≥9 + import 全绿（flatbuffers/onnx/onnxruntime/pydantic/pytest_asyncio/pytest_timeout/numpy）+ `bin/python3` 存在（python3 shim 由 venv 自带）。
4. **基线重跑**（T3）：sz0001 上 `PATH=~/venvs/fmpytest/bin:$PATH PYTHONPATH=sim:gen ~/venvs/fmpytest/bin/python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors --ignore=<5 个 cocotb 文件>` + `verify_ops_func_model.py` 五判定行 + 三个新 FM 文件（113/13/6）；evidence `.omo/evidence/sz0001-fmenv-baseline.txt` 判定行 `SZ0001-FMENV-BASELINE: <n>-passed (<m> failed/<k> skipped/<e> errors)` / `SZ0001-FMENV-VERIFY: 5/5 PASS`；逐项 triage（期望残留仅：8F 漂移、stale spike pin、glibc 二进制类、5 cocotb collection error——`--ignore` 后应消失）。
5. **政策落档**（T4）：AGENTS.md 政策 bullet 升级为可执行口径：FM pytest 在 sz0001 用 `~/venvs/fmpytest`（给出确切命令模板）+ `--ignore` 5 cocotb 文件；RTL/VCS/cocotb 仍 sz0001 py3.11 流程；spike/llama 依赖测试暂留 sz0002（open item，待工具链齐备后单独计划）。

### Must NOT have
- 不碰 /NAS/Tools root 环境；不 sudo；不 yum/conda 安装。
- 不改 rtl/sim/firmware/scripts 产品代码；不改既有测试。
- 不 push；不动 7 个 dirty 文件（全量套件会确定性重写，不提交）。
- 若 P0 发现两机均无 PyPI 且无 wheel 缓存 → **STOP 上报**（需要 IT/内网镜像决策），不硬造环境。

## Decisions to sanity-check（待用户批准后执行）
- **D1 wheel 源**（**用户已确认 2026-09-04**）：sz0002 可访问外网（P0 实测 network=yes）；wheel 在 sz0002 pip 下载到 **`<repo>/build/wheels`（NFS 共享树内、gitignore 覆盖）**——P0 实测 `~/venvs` 不跨机共享（sz0001 看不到 marker），sz0001 从仓库树同一路径直接安装，无需搬运。
- **D2 5 个 cocotb pytest 文件**：`test_cv_conv2d_rtl.py`、`test_soc_pcie_dma.py`、`wrapper/test_{mxu,sfu,vector}_wrapper.py` 在 FM pytest 基线中经 `--ignore` 排除——它们当前在 sz0002（无 cocotb）和 sz0001（pytest 环境无 cocotb）都从未跑过，属 RTL/cocotb 验证域（VCS 流程 py3.11），不作为 FM 基线的一部分。
- **D3 spike/llama 依赖测试**（glibc 2.17 vs 2.28 二进制不兼容）：暂留 sz0002，记 open item；sz0001 本地重建（gcc 9.3.1 devtoolset-9）放后续独立计划。NFS 共享树上的 `spike_src/build` 同一路径无法同时服务两机，重建需先解决产物路径分离（届时再定）。
- **D4 onnxruntime 依赖测试**（**计划新增，同 D2/D3 模式**）：onnxruntime 无 glibc 2.17 兼容 wheel（manylinux2014 不存在、2_27/2_28 需 glibc≥2.27），其依赖测试文件（以 grep `onnxruntime` 命中的 sim/tests 文件为准，预计 test_gen_cv_golden 等）随 D2 的 --ignore 清单一并排除、留 sz0002；onnx 本身（manylinux2010 wheel，glibc 2.12+ 可加载）照常安装。

## Execution strategy
- Wave 1：P0 探测（worker；失败路径 STOP 上报）。
- Wave 2：T1（wheel 集齐，依赖 P0）∥ 无他（单 todo 波）。
- Wave 3：T2（venv 搭建，依赖 T1）∥ T3 准备（evidence 模板）。
- Wave 4：T3（基线重跑，依赖 T2；~1h）。
- Wave 5：T4（政策落档 AGENTS.md，依赖 T3 判定）。
- 终审波：F1-F4 并行。

## Todos
- [x] 0. P0 快速确认：sz0002 网络 + NFS 共享 + 已装包清单 + pip 缓存（实证落档）
  What to do: sz0002 上只读探测——(1) `curl -sI --connect-timeout 5 https://pypi.org/simple/ | head -1` 与 `pip download --no-deps -d /tmp/probe pytest==9.0.0 2>&1 | tail -3`（30s 超时保护）；(2) NFS 共享测试：sz0002 `touch ~/venvs/wheels/.probe-$RANDOM` 后 sz0001 `ls ~/venvs/wheels/.probe-*`（互见=共享，用户已确认预期 yes）；(3) sz0002 `python3 -m pip list 2>/dev/null | grep -i -E 'flatbuffers|onnx|pytest|pydantic|numpy|protobuf|pyyaml'`；(4) `ls ~/.cache/pip 2>/dev/null | head`、`find ~/.cache/pip -name '*.whl' 2>/dev/null | head -20`。落档 `.omo/evidence/sz0001-fmenv-p0.txt`（判定行 `FMENV-P0: network=<yes|no> nfs=<shared|not-shared> wheels=<n-cached>`）。
  Acceptance: evidence 存在且四问均有实测答案；`FMENV-P0:` 判定行存在。
  Commit: Y | chore(omo): sz0001 FM-env P0 probe evidence

- [x] 1. wheel 集齐（pytest≥9 全家桶 + onnx 栈，目标 cp312/manylinux2014）
  What to do: 按 P0 结果选源；优先 `pip download --only-binary=:all: --platform manylinux2014_x86_64 --implementation cp --python-version 312 --no-deps -d ~/venvs/wheels <pkg>` 逐个下载（pytest、pytest-asyncio、pytest-timeout、pydantic、numpy、flatbuffers、onnx、onnxruntime、protobuf、pyyaml、typing_extensions、packaging、pluggy、iniconfig、exceptiongroup、idna、anyio、coloredlogs、flatbuffers 依赖等——先 `--no-deps` 逐个，缺失依赖补下）；若源无 PyPI 但有已装环境：`pip download` 同版或 `pip wheel` 从 site-packages 打包。落档 wheel 清单 `.omo/evidence/sz0001-fmenv-wheels.txt`。
  Acceptance: `ls ~/venvs/wheels/*.whl | wc -l` ≥ 目标集数量；清单含版本号；判定行 `FMENV-WHEELS: <n>-wheels ready`。
  Commit: Y | chore(omo): sz0001 FM-env offline wheel set

- [ ] 2. sz0001 建 venv + 离线安装 + 冒烟
  What to do: ssh sz0001——`/NAS/Tools/anaconda3/bin/python -m venv ~/venvs/fmpytest`；在 `<repo>/build/wheels` 写 `requirements-fmpytest.txt`（**onnx 钉死 ==1.19.1**：T1 实测 ≥1.20 的 cp312 wheel 只有 manylinux_2_27/2_28，glibc 2.17 不可加载——与仓库 requirements.txt `>=1.22` 的偏离在 evidence 与 AGENTS.md 政策中记录）；`~/venvs/fmpytest/bin/pip install --no-index --find-links <repo>/build/wheels -r <repo>/build/wheels/requirements-fmpytest.txt`；冒烟脚本：`~/venvs/fmpytest/bin/python -c "import pytest, flatbuffers, onnx, pydantic, pytest_asyncio, pytest_timeout, numpy, google.protobuf, yaml; print(pytest.__version__, numpy.__version__, onnx.__version__)"` 且 `pytest --version`≥9；`ls ~/venvs/fmpytest/bin/python3` 存在。落档 `.omo/evidence/sz0001-fmenv-venv.txt`（判定行 `FMENV-VENV: ready`）。
  Acceptance: 冒烟 import 全绿；pytest≥9；python3 shim 就位；venv 无 root 依赖改动。
  Commit: Y | chore(omo): sz0001 FM-env venv smoke evidence

- [ ] 3. sz0001 新环境基线重跑 + 取证
  What to do: ssh sz0001——`PATH=/home/zhengs/venvs/fmpytest/bin:$PATH PYTHONPATH=sim:gen /home/zhengs/venvs/fmpytest/bin/python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors --ignore=sim/tests/test_cv_conv2d_rtl.py --ignore=sim/tests/test_soc_pcie_dma.py --ignore=sim/tests/wrapper/test_mxu_wrapper.py --ignore=sim/tests/wrapper/test_sfu_wrapper.py --ignore=sim/tests/wrapper/test_vector_wrapper.py`；`PYTHONPATH=sim ~/venvs/fmpytest/bin/python scripts/verify_ops_func_model.py`；三个新 FM 文件单独跑。evidence `.omo/evidence/sz0001-fmenv-baseline.txt`（判定行 `SZ0001-FMENV-BASELINE:` / `SZ0001-FMENV-VERIFY: 5/5 PASS` / 113/13/6）；逐项 triage 并对照 `.omo/evidence/sz0001-baseline-rerun.txt` 算 delta（预期消失：flatbuffers×17、onnx×38、python3×44、soc_diff×4；预期残留：8F、stale pins、glibc 二进制类）。
  Acceptance: 判定行齐全；delta 分类明确；零 FM-op 回归；`git diff HEAD^ HEAD --name-only` == 仅 evidence。
  Commit: Y | chore(omo): sz0001 FM-env baseline re-run evidence

- [ ] 4. AGENTS.md 政策升级为可执行口径
  What to do: 把 2026-09-04 政策 bullet 改为：FM pytest 在 sz0001 经 `~/venvs/fmpytest`（命令模板：`PATH=~/venvs/fmpytest/bin:$PATH PYTHONPATH=sim:gen ~/venvs/fmpytest/bin/python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors --ignore=<5 cocotb 文件>`）；RTL/VCS/cocotb 仍在 sz0001 py3.11 VCS 流程；spike/llama 依赖测试暂留 sz0002（open item：glibc/toolchain 待齐）。COMMANDS 段同步。只提交 AGENTS.md。
  Acceptance: `git diff HEAD^ HEAD --name-only` == `AGENTS.md`；政策含命令模板与 open item。
  Commit: Y | docs(agents): executable FM-pytest policy — sz0001 fmpytest venv + ignore-list (2026-09-04)

## Final verification wave
- [ ] F1. 合规审计：todos 0-4 各一原子 commit、message 对应、判定行齐全、evidence 存在。
- [ ] F2. 质量复核：venv 冒烟输出真实（非伪造）；wheel 清单与安装一致；--ignore 五文件与 D2 一致。
- [ ] F3. 人工复验：ssh sz0001 上 `~/venvs/fmpytest/bin/pytest --version` 与 evidence 一致；verify_ops 五判定行原文抽查。
- [ ] F4. 范围保真：变更集 ⊆ {AGENTS.md, .omo/evidence/sz0001-fmenv-*, .omo/plans/sz0001-fm-env-setup.md, .omo/drafts/*}；/NAS/Tools、rtl/sim/firmware/scripts 零改动；未 push。

## Commit strategy
- 一 todo 一原子 commit（message 见各 todo）；pathspec 显式；不 push；venv/wheel 属用户目录不提交。

## Success criteria
1. `FMENV-VENV: ready` + `SZ0001-FMENV-BASELINE:` 判定行落档；环境性失败消除（对照 delta 明确）。
2. AGENTS.md 政策可执行（命令模板 + 5 文件 ignore 清单 + spike/llama open item）。
3. F1-F4 全 APPROVE；零产品代码改动；未 push。

# func-model-gap-closure - Work Plan

## TL;DR (For humans)

审计和补完 Func Model 的 SoC 数据路径 gap。关键发现：大部分 gap 已部分落地——MMIOBridge 已走 CrossbarModel、PCIeModel 已存在且集成、FuncModel 已有 crossbar 实例。剩余工作是：(1) 建 APBPeripheral 基类统一 MMIO 寄存器模型，(2) 让 Spike 的 SRAM/DRAM 访问经 CrossbarModel，(3) 清理 NPUFirmware 冗余路径，(4) 全量回归验证。

**What you'll get**：Spike firmware 对 SRAM/DRAM 的访问路由到 CrossbarModel (MASTER_IBEX)，APB MMIO 寄存器有统一的 register model，NPUFirmware 标记 deprecated。

**What it will NOT do**：不修改 RTL/固件源码，不涉及性能建模，不升级 RISCVMini，不改 PCIeModel（已完整）。

**Effort**：4 个 todo，1-2 轮 agent 执行。

**Key decisions**：CrossbarModel/PCIeModel 已存在，以审计+补全代替从零构建；Spike 通过 `spike_mmio_server.py`（非 plugin C++ 源码）接入 CrossbarModel。

---

## Scope

### In
- `sim/models/apb_peripheral.py` — 新建 APBPeripheral 基类 + 8 个 engine register bank
- `sim/spike_mmio_server.py` — `_normalize_addr` 改造：SRAM 地址直接偏移 → CrossbarModel.MASTER_IBEX 路由
- `sim/miniv.py` — `NPUFirmware` 加 `DeprecationWarning`
- 回归验证：全量 pytest + FM-SOC + Spike E2E

### Out
- RTL 源码（`rtl/`）
- C firmware 源码（`firmware/`）
- Spike plugin C++ 源码（`spike_src/plugins/npu_mmio_plugin.cc`），只改 Python server
- PCIeModel（`sim/models/pcie.py`）——已完整，不修改
- MMIOBridge engine 数据路径（`sim/mmio_bridge.py` `_handle_*`）——已走 CrossbarModel，不重做
- Gap #9（IRQ-Chain）——Spike 已有标准 RISC-V trap 机制

### Constraints
- 所有工作只在 `sim/` 目录内
- 已有 pytest 回归必须保持全量通过
- Spike 插件 ABI（`-D_GLIBCXX_USE_CXX11_ABI`）已在 Phase 7 验证，不重做

---

## Verification strategy

- **Agent-executed QA**：每 todo happy + failure + anti-vacuous 测试
- **回归基线**：`PYTHONPATH=sim python -m pytest sim/tests/ -q`，当前通过数不得减少
- **Spike E2E**：`PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj` PASS
- **FM-SOC 回归**：`bash sim/regression/run_fm_soc_all.sh`（在 sz0001 上）

---

## Execution strategy

- 顺序执行，每 todo 单 agent
- 每 todo 完成后 git commit
- 全部完成后做 Final Verification Wave

---

## Todos

- [x] 1. 新建 APBPeripheral 基类 + 8 engine register bank（Gap #1 Close）

- **References**:
  - `sim/regmap.py`：MXU/SFU/VECTOR/DMA/DOORBELL/INTC 寄存器偏移定义
  - `sim/models/crossbar.py:205-213`：APBDecoder SLAVES（目前 7 slaves：MXU/SFU/VECTOR/DMA/PCIe/DOORBELL/INTC，缺少 PCIE_DMA）。RTL `rtl/soc/apb_decoder.v:2,8-15` 有 8 个 slave（含 PCIE_DMA at 0x4000_7000）
  - `sim/regmap.py:22-23,87-91`：`PCIE_BASE=0x4000_4000`（无 PCIE 寄存器类），`PCIE_DMA_BASE=0x4000_7000`（有 PCIE_DMA 类，含 CTRL/CMD/STATUS/DIM0/DIM1/SRC/DST/SIZE 等）
  - `sim/mmio_bridge.py`：当前 MMIO handle 使用 `self._status[addr]` 字典（L62-76）实现寄存器读写——无 type safety、无 field-level access、无 callback。_handle_* 方法（L168-555）各自内联重复 CTRL/CMD/STATUS/DIM field 解析逻辑
  - `docs/soc-fm-gap-spec.md:440-561`：APBPeripheral API 设计 spec
  - `rtl/soc/apb_decoder.v`：RTL APB decoder psel/paddr decode 逻辑参考

- **Acceptance criteria**:
  - 文件：`sim/models/apb_peripheral.py`
  - `RegisterField(name, offset, default=0, access='rw', callback=None)` dataclass
  - `APBPeripheral(name, base_addr, fields: list[RegisterField])` 基类，提供：
    - `read(offset: int) -> int` — 按 byte offset 读寄存器值
    - `write(offset: int, value: int)` — 写寄存器值；`access='w'` 触发 `callback(value)`；`access='r'` 静默忽略；`access='w1c'` 实现 write-1-to-clear
    - `read_field(name: str) -> int` — 按名称读
    - `write_field(name: str, value: int)` — 按名称写
  - 8 个工厂函数，匹配 RTL apb_decoder.v 的 8 个 slave：`make_mxu_peripheral()`, `make_sfu_peripheral()`, `make_vector_peripheral()`, `make_dma_peripheral()`, `make_pcie_peripheral()`, `make_doorbell_peripheral()`, `make_intc_peripheral()`, `make_pcie_dma_peripheral()`
  - 每个工厂函数用 `RegisterField` 定义该 engine 的完整寄存器集，fields 与 `sim/regmap.py` 一致
  - 注：`make_pcie_peripheral()`（slave4, 0x4000_4000）对应 RTL 的 PCIe_EP slave。`regmap.py` 目前无 PCIE 寄存器类——使用 `sim/models/pcie.py:13-25` 的 `PCIeState` dataclass 字段（completer_id, max_payload_size, msix_enable, irq_enable, irq_pending, bar0/bar1_base+mask）定义寄存器集
  - 注：`make_pcie_dma_peripheral()`（slave7, 0x4000_7000）对应 RTL 的 PCIE_DMA slave，fields 从 `sim/regmap.py:90-102` 的 `PCIE_DMA` 类提取
  - Python `APBDecoder`（`sim/models/crossbar.py:205-213`）同步更新：从 7 slaves 补齐为 8（添加 `7: ("PCIE_DMA", Addr.PCIE_DMA_BASE)`），`decode()` 中 page 范围从 `0xF` 改为验证 `page <= 7`
  - 注：本 todo 只建 APBPeripheral 类 + 工厂函数 + APBDecoder 补齐。不改 MMIOBridge——已有 `_status[addr]` 路径保持原样

- **QA**:
  - Happy: `periph.write(0x04, 0xDEAD)` → `periph.read(0x04) == 0xDEAD`；`periph.write_field("CMD", 1)` triggers callback
  - Failure: `periph.write(0x100, 0)` → `ValueError`（offset out of 4KB window）；write to `access='r'` register → value unchanged
  - Anti-vacuous: write to DIM0 → DIM1 unchanged
  - Evidence: `PYTHONPATH=sim python -m pytest sim/tests/test_apb_peripheral.py -v`

- **Commit**: `feat(models): add APBPeripheral base class with 8 engine register banks`

---

- [x] 2. Spike firmware SRAM/DRAM 访问经 CrossbarModel（Gap #2 Close）

- **References**:
  - `sim/spike_mmio_server.py`：Unix socket server — `_handle_request()` (L55-74) 解析 `R/W` 命令，`_normalize_addr()` (L48-52) 将 0x20000000+ 地址减去 SRAM_FIRMWARE_BASE 转为 bytearray offset，然后调 `bridge.handle('read'/'write', addr, value)`
  - `sim/spike_firmware.py:22,60-82,231`：SpikeFirmware 类 — 是 NPUFirmware 的 drop-in 替换，`__init__` 接收 `sim_modules`（L62）和 `serve_fn`（L67），内部在 L231 调用 `self._serve_fn(self.bridge, ...)`。当前不传 crossbar
  - `sim/mmio_bridge.py:15,34-35,38`：MMIOBridge 已有 `_crossbar` property（`self.modules.get('crossbar')`），返回 Optional[CrossbarModel]
  - `sim/models/crossbar.py`：CrossbarModel.read/write API 接受 `master_id` 参数；MASTER_IBEX=0
  - `sim/func_model.py:33,43-48`：FuncModel 已创建 `self.crossbar` 并传递给 MMIOBridge via `modules['crossbar']`
  - 当前数据流：Spike plugin → socket → `spike_mmio_server._handle_request` → `bridge.handle`。MMIO 寄存器访问（0x40000000+）正确——bridge.handle 路由到对应 engine。但 firmware 对 SRAM（0x20000000-0x203FFFFF）和 DRAM（0x80000000+）的 load/store（如 descriptor read、ring buffer write）调 `bridge.handle`，而 bridge 只处理 MMIO 地址，non-MMIO 地址在 handle 中走不到正确的内存路径

- **Acceptance criteria**:
  - `spike_mmio_server._handle_request()` 修改：对于 non-MMIO 地址（< 0x40000000 或 >= 0x80000000），不使用 `bridge.handle`，改为通过传入的 CrossbarModel 实例路由：
    - SRAM 地址（`SRAM_FIRMWARE_BASE <= addr < SRAM_FIRMWARE_END`）→ `crossbar.read/write(MASTER_IBEX, addr, size)`，其中 addr 保持 SoC 绝对地址（0x20000000+）
    - DRAM 地址（addr >= 0x80000000）→ `crossbar.read/write(MASTER_IBEX, addr, size)`
  - `spike_mmio_server.py` 接收 `crossbar` 参数（从 FuncModel 传入）
  - `_normalize_addr()` 移除——不再需要 SRAM 地址偏移转换
  - MMIO 地址（0x40000000-0x7FFFFFFF）保持原路径：`bridge.handle('read'/'write', addr, value)`
  - `serve()` 函数签名更新：新增 `crossbar: Optional[CrossbarModel] = None` 参数；crossbar 为 None 时仅处理 MMIO 请求，non-MMIO 请求返回 ERR
  - `spike_mmio_server.py:159`（`main()` 中的 `serve()` 调用）和 `spike_host.py`（`spike_host.py` 中 `serve()` 调用，约 L931）更新：传入 `model.crossbar`
  - `sim/spike_firmware.py` 更新：`SpikeFirmware.__init__` 中从 `sim_modules` 获取 `crossbar`（`self.crossbar = sim_modules.get('crossbar')`），L231 `self._serve_fn(...)` 调用传入 `crossbar=self.crossbar`

- **QA**:
  - Happy: `PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj` — PASS
  - Happy: `PYTHONPATH=sim python3 sim/spike_host.py --mode chain --ops mmul,sfu,vector` — PASS
  - Failure: crossbar 为 None → 清晰的 RuntimeError（不能回退到旧路径，因为旧路径依赖 `_normalize_addr` 的 SRAM offset 语义，与 CrossbarModel 的 SoC 绝对地址语义不同）
  - Evidence: `PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke ... 2>&1 | tee build/evidence/wave2-spike-crossbar.txt`

- **Commit**: `feat(spike_mmio): route firmware SRAM/DRAM access through CrossbarModel.MASTER_IBEX`

---

- [x] 3. NPUFirmware 加 DeprecationWarning（Gap #11 Close）

- **References**:
  - `sim/miniv.py:434-719`：NPUFirmware 类，Python 重写的固件逻辑，包含 `run_loop()`, `_dispatch()`, `_dram_read()`, `_wait_done()`, `dispatch_interrupt()`
  - `sim/func_model.py:58,128-138`：`_create_firmware()` 根据 `use_spike` flag 选择 NPUFirmware 或 Spike 路径。当前默认 `use_spike=False`（NPUFirmware 是默认）
  - `sim/spike_host.py`：Spike E2E 入口（已验证 FM-SOC-027/032/10X）
  - Spike 已通过 T2 接入 CrossbarModel，成为完备的 golden 固件路径

- **Acceptance criteria**:
  - `NPUFirmware.__init__` 顶部加：`warnings.warn("NPUFirmware is deprecated; use Spike + real firmware ELF for golden reference verification. NPUFirmware remains available for fast smoke tests.", DeprecationWarning)`
  - `NPUFirmware._dispatch` docstring 更新：加 `DEPRECATED` 标记，指向 `spike_host.py` 作为替代
  - `NPUFirmware` 所有 public 方法不改变行为——deprecated ≠ removed，已有测试继续通过
  - （可选，低优先级）`FuncModel._create_firmware` 中 `use_spike` 默认值改为 `True`——不影响已有调用点，但新代码默认走 Spike

- **QA**:
  - Happy: `python3 -W default::DeprecationWarning -c "from sim.miniv import NPUFirmware"` → 输出 DeprecationWarning，exit 0
  - Happy: `PYTHONPATH=sim python -m pytest sim/tests/ -q` → 全部通过（NPUFirmware 仍可正常工作）
  - Evidence: `PYTHONPATH=sim python -m pytest sim/tests/ -q -W default::DeprecationWarning 2>&1 | grep DeprecationWarning | head -5`

- **Commit**: `chore(firmware): add DeprecationWarning to NPUFirmware, document Spike as golden path`

---

- [x] 4. 全量回归 + CrossbarModel 路由完整性验证

- **References**:
  - `sim/mmio_bridge.py:180-243`：MXU data path（已验证走 crossbar，MASTER_MXU）
  - `sim/mmio_bridge.py:318-328`：SFU data path（已验证走 crossbar，MASTER_SFU）
  - `sim/mmio_bridge.py:416-427`：Vector data path（已验证走 crossbar，MASTER_VEC）
  - `sim/mmio_bridge.py:520-525`：DMA data path（已验证走 crossbar，MASTER_DMA）
  - `sim/mmio_bridge.py:597-605`：`_get_mem()` / `_translate_addr()` fallback 方法仍存在
  - `sim/models/crossbar.py`：CrossbarModel 已有 read/write/decode/grant/APBDecoder
  - `sim/models/pcie.py`：PCIeModel 已存在（920 行），已集成到 FuncModel（`sim/func_model.py:34`）
  - `rtl/testcase-list-soc-fm.md`：33 FM-SOC cases（27 PASS, 6 SKIP）
  - `spike_src/plugins/Makefile`：plugin 编译配置（不在此计划修改）

- **Acceptance criteria**:
  - `PYTHONPATH=sim python -m pytest sim/tests/ -q` — 全部通过，无回归
  - `PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj` — PASS
  - `PYTHONPATH=sim python3 sim/spike_host.py --mode forward --layers 2 --token-ids 1,2,3 --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --reference llama_ref/refs/qwen_l0_l1_hidden.npz` — 正常完成，无 crash
  - 新增验证：`python3 -c "from sim.func_model import FuncModel; fm = FuncModel(); assert fm.crossbar is not None; assert fm.pcie is not None; print('FuncModel crossbar+pcie OK')"`
  - 新增验证：确认 MMIOBridge `_handle_*` 中 4 条 engine 数据路径（MXU/SFU/Vector/DMA）均在 crossbar 可用时走 crossbar，crossbar 缺失时回退到旧路径（`_get_mem`/`_translate_addr`）
  - `_get_mem()` 和 `_translate_addr()` 保留但加 docstring 标注 fallback-only

- **QA**:
  - Happy: 全量 pytest + Spike E2E 均通过
  - Happy: crossbar grants 历史中包含 MASTER_MXU/SFU/VEC/DMA/IBEX 的访问记录（通过 T2 后的 Spike E2E）
  - Failure: 移除 `modules['crossbar']` → MMIOBridge 回退到 `_get_mem` 旧路径 → Spike E2E crash（因为 spike_mmio_server 依赖 crossbar）
  - Evidence: `PYTHONPATH=sim python -m pytest sim/tests/ -q --tb=short 2>&1 | tail -5`

- **Commit**: `test(gap-closure): full regression gate — CrossbarModel + Spike + PCIeModel audit`

---

## Final verification wave

- [x] F1. Plan compliance audit: 所有 todo checkbox `[x]`；evidence 文件匹配 acceptance criteria；commit message 匹配 commit strategy
- [x] F2. Code quality review: `python3 -m compileall sim/models/apb_peripheral.py sim/spike_mmio_server.py sim/miniv.py` — 零 syntax error；无 import RTL 路径
- [x] F3. Real manual QA on sz0001: Spike plugin 可编译 → `spike_host.py --mode mmul_smoke` PASS → FM-SOC 全量回归 PASS
- [x] F4. Scope fidelity: `git diff --stat HEAD~N..HEAD` 只在 `sim/` 目录内

---

## Commit strategy

| Task | Commit | Message |
|------|--------|---------|
| 1 | Y | `feat(models): add APBPeripheral base class with 8 engine register banks` |
| 2 | Y | `feat(spike_mmio): route firmware SRAM/DRAM access through CrossbarModel.MASTER_IBEX` |
| 3 | Y | `chore(firmware): add DeprecationWarning to NPUFirmware, document Spike as golden path` |
| 4 | Y | `test(gap-closure): full regression gate — CrossbarModel + Spike + PCIeModel audit` |
| F1-F4 | N | (verification only) |

---

## Success criteria

- [ ] `PYTHONPATH=sim python -m pytest sim/tests/ -q` — 全部通过，无回归
- [ ] `PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke ...` PASS
- [ ] `python3 -W default::DeprecationWarning -c "from sim.miniv import NPUFirmware"` → 触发 DeprecationWarning
- [ ] `sim/spike_mmio_server.py` 中 `_normalize_addr()` 已移除，SRAM/DRAM 地址通过 CrossbarModel 路由
- [ ] `python3 -c "from sim.func_model import FuncModel; fm = FuncModel(); assert fm.crossbar is not None; assert fm.pcie is not None"`
- [ ] `git diff --stat main..HEAD` 只包含 `sim/` 目录

# func-model-gap-closure — Draft

## Metadata
- **intent**: clear
- **review_required**: false
- **slug**: func-model-gap-closure
- **status**: approved
- **plan_artifact**: .omo/plans/func-model-gap-closure.md
- **approved_at**: 2026-07-26

## Decisions
1. **Golden firmware engine**: Spike + 真实 C firmware ELF。不升级 RISCVMini，不维护 NPUFirmware 作为替代路径。
2. **CrossbarModel**: 复用现有 `sim/models/crossbar.py` 骨架（已完成 read/write/decode/grant/APBDecoder）。
3. **APBPeripheral**: 新增 `sim/models/apb_peripheral.py`，统一 7 个 engine 的 MMIO 寄存器模型。
4. **PCIeModel**: 新建 `sim/models/pcie.py`，TLP 构建/解析 + BAR 路由。
5. **Scope 边界**: 不修改 RTL，不修改 C firmware 源码（`npu_firmware.c`），不修改 Spike 源码（仅重建 plugin `.so`）。

## Pending
- User approval before writing plan file.

## Approach
三波执行：
1. MMIOBridge 挂 CrossbarModel + APBPeripheral 基类（Gap #8 + #1）
2. Spike plugin 通过 CrossbarModel 访问内存（Gap #2）
3. PCIe TLP 模型 + 清理 NPUFirmware/RISCVMini 冗余路径（Gap #7 + #11）

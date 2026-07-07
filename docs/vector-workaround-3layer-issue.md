# Vector Wrapper Workaround 及其在 3-Layer 验证中的暴露

> **日期**: 2026-07-07
> **相关 Bug**: BUG-RTL-SOC-005
> **相关 Plan**: `soc-verification-gaps-phase5` W1.3

---

## 一、背景：单层 17-op 验证中的 workaround

在 Phase 4 的 SoC RTL 验证中，FM-SOC-027（blk.0 17-op chain）**全部 17/17 PASS**。但这个 PASS 不是没有 bug，而是依赖了一个 workaround。

### 1.1 Bug 根因（BUG-RTL-SOC-005）

`rtl/wrapper/vector_soc_wrapper.v` 在读写 SRAM/DRAM 时，**固定读写 512-byte chunk**，不考虑实际请求的字节数。当 Vector op 的 `elements` 较小（如 64 个 INT32 = 256 bytes），wrapper 实际会写满 512 bytes，超出范围的 256 bytes 被覆盖为垃圾值。

如果下一条 Vector op 的输入地址恰好落在这 256 bytes 的污染区域内，就会读到错误数据。

### 1.2 单层时的 workaround

在 `rtl_soc_runner.py` 的 `_build_027()` 中，手工给每条 Vector op 分配了**不重叠的独立 SRAM 地址**（间隔 ≥ 0x800），确保没有任何两条 Vector op 的地址区域有重叠。

```python
# FM-SOC-027 中的做法（示意图）
op14 VMUL:   a_addr = SRAM + 0x4000,  b_addr = SRAM + 0x4400,  o_addr = SRAM + 0x4800
op16 VRESID: a_addr = SRAM + 0x4C00,  b_addr = SRAM + 0x5000,  o_addr = SRAM + 0x5400
                                                    ↑ 间隔 0x800，512-byte chunk 不重叠
```

因为 Vector op 都使用了独立地址，wrapper 的 512-byte 固定写入互相不干扰，workaround 拦截了 bug。

### 1.3 Workaround 的本质问题

这不是 fix，而是**外部规避**。Firmware 不应该知道 "Vector wrapper 内部是固定写 512-byte" 这个实现细节。正确的行为是：RTL 根据 `elements` 参数 mask 掉超出有效范围的 store beats。

---

## 二、3-Layer 验证暴露的问题

Phase 5 W1.3 对 Qwen2.5-3B 的前 3 层跑了完整的 forward pass（51 ops）。结果：**45 PASS，6 FAIL**。

### 2.1 失败分布

| 失败 op | Layer 0 | Layer 1 | Layer 2 | cycles | 特征 |
|---------|:---:|:---:|:---:|:---:|------|
| **attn_weight** | op07 ❌ | op24 ❌ | op41 ❌ | **0** | op 根本没执行 |
| **VMUL gate\*up** | op14 ❌ | op31 ❌ | op48 ❌ | 23,814 | 执行了但结果错 |

### 2.2 VMUL gate\*up — workaround 失效

单层 FM-SOC-027 用 workaround 把 VMUL 跑通了。3-layer 的 runner 没有对应的地址隔离逻辑（3-layer 的所有 op 地址是自动分配的），导致跨层时 Vector 地址重叠，wrapper 的 512-byte 固定写入污染了下一条 VMUL 的输入。

**为什么 3-layer 比单层更容易触发**：3-layer × 17 ops = 51 ops，SRAM 地址分配密得多。单层 17 条时手工隔开地址还勉强可行，51 条时手工分配不现实——这正是 workaround 的脆弱点。

### 2.3 attn_weight — 另一个独立问题

`attn_weight` 三条 op 的 `cycles=0`，说明 op 在 MMU 配置阶段就失败了——firmware 写 CMD.START 后 STATUS.BUSY 从未拉高（或 firmware dispatch 压根没跑到这条 op）。根因方向：
- firmware ring buffer 在 51 cmd 时溢出了 32-entry ring
- 或者 op 的权重 preload 地址越界触发了异常

这个和 VMUL 是**两个独立问题**，不是同一个 workaround 失效造成的。

---

## 三、影响评估

| 影响 | 当前严重度 | 说明 |
|------|:---:|------|
| **Vector 验证可靠性** | 🔴 高 | workaround 在跨层场景下系统性失效，3 层 6 FAIL 中有 3 条是直接后果 |
| **Firmware 接口不透明** | 🟡 中 | firmware 无法依赖"配地址就能正确执行"的硬件契约 |
| **FPGA/tape-out 阻碍** | 🔴 高 | workaround 不能上 FPGA，更不可能在 ASIC 上要求 Host 软件手工隔离地址 |
| **36-layer 验证** | 🔴 高 | 612 ops 不能靠手工隔地址跑通 |

---

## 四、修复方案

### 4.1 VMUL gate\*up — 修 RTL（推荐，永久方案）

修改 `rtl/vector/vector_top.v` 或 `rtl/wrapper/vector_soc_wrapper.v`，在 store 阶段按 `elements` 实际字节数 mask 掉超范围的 beat。

```verilog
// 当前（简化的）:
always @(posedge clk) begin
    sram_wdata <= chunk;       // 写满 512-bit
    sram_wstrb <= 64'hFFFF_FFFF_FFFF_FFFF;  // 全 byte enable
end

// 修后:
wire [5:0] valid_bytes = elements * 4;  // INT32 = 4 bytes each
always @(posedge clk) begin
    sram_wdata <= chunk;
    sram_wstrb <= (64'hFFFF_FFFF_FFFF_FFFF >> (64 - valid_bytes));  // mask 超范围
end
```

修完后 workaround 可以删除，FM-SOC-027 重跑验证无退化。

### 4.2 attn_weight — 先定位根因

`cycles=0` 意味着硬件根本没执行。需要读仿真日志确认是：
- firmware ring buffer 溢出 → 扩大 RING_ENTRIES
- 权重地址越界 → 检查 3-layer runner 的 DRAM/SRAM 地址分配
- MMU CMD.START 被某种条件阻塞 → 检查 `start_hold` 等信号

---

## 五、结论

Phase 4 的 "33/33 PASS" 中有 workaround 维护的假 PASS。3-layer 验证撕掉了这个 workaround，暴露了 RTL 的底层 bug。**这个 bug 应该在 Phase 5 修复，不应该再被 workaround 绕过。** 修复成本低（改 `vector_alu.v` 的 store beat mask），收益大（删掉 workaround + 3-layer VMUL 全部 PASS + FPGA 可部署）。

attn_weight 是独立问题，需要单独 debug。两个修完后 3-layer 预期从 45/51 → 51/51 PASS。


## [2026-09-25] F 波与签署门
- F1-F4 **全部 APPROVE**（F2 经 Round-1 REJECT → `ad30841` 修复 → 复审 APPROVE；F3 首跑中断 → 重跑 APPROVE）。
- 交付：分支 `wrp-defects-and-tooling-fixes` @ `ad30841`，8 commit；模块级台账 **6 Fixed / 0 Open**；wrapper 套件 **6/6**；FM-SOC 25/8/0/0/33（新二进制 `d516a216`）。
- **计划 F1-F4 标记 `- [~]`**：勾选与合并被 Final Wave 门阻塞于用户 explicit okay（用户-only 决策）；批准后 = 勾选 → 提交 `.omo` 记账 → `--no-ff` 合并（不 push）。
- 非阻塞遗留：F3 LOW(`run_fm_soc_all.sh` 缺 host 转发)、I-1(sz0001 自环 key)、I-13(`results.xml` 被碰脏已还原)、I-20(`nohup` 丢 rc)、R1(conformance TB 行号冻结)、K>128 不支持、IRQ 未改（标记在案）、SFU 6/7 + Vector 5/6 为首次可见真实结论。

# 修复 boulder.json 卡住的 completion nudge

## TL;DR
> Summary:      `.omo/boulder.json` 的 `active_work_id` 仍指向已完成的旧计划，导致 BOULDER COMPLETE nudge 反复触发。将状态机复位为 idle，并把新计划 `dse-funcmodel-prefill-closure` 补登记为 completed work。
> Deliverables: boulder.json 复位（active_work_id=null，顶层镜像字段清理），新计划 work 条目补登记。
> Effort:       XS（单文件 JSON 编辑）
> Risk:         Low - boulder.json 只是状态追踪文件，不影响代码/证据。

## Scope
### Must have
- `.omo/boulder.json`：
  - `active_work_id` 置为 `null`。
  - 顶层镜像字段（`active_plan` / `plan_name` / `status` / `started_at` / `updated_at` / `agent` / `ended_at` / `elapsed_ms`）移除或设为 idle 语义，不再指向旧计划。
  - `works` 字典保持完整（作为历史归档，一个条目都不删）。
  - 在 `works` 中补登记 `dse-funcmodel-prefill-closure` 条目（status=completed，active_plan 指向 `.omo/plans/dse-funcmodel-prefill-closure.md`，started_at/ended_at 用 2026-08-13 的实际执行时间，task_sessions 留空或从本会话各 todo 的 subagent session id 汇总）。
- 验证 JSON 合法（`python3 -m json.tool` 解析通过）且 `active_work_id == null`。

### Must NOT have
- 不删除/改写 `works` 中任何已有历史条目的字段。
- 不改动任何代码、证据、报告、规格文件。
- 不新建 git commit 之外的副作用（此改动自身可单独 commit 或并入下一次提交）。

## Verification strategy
- QA: `python3 -m json.tool .omo/boulder.json` exit 0；`python3 -c "import json;d=json.load(open('.omo/boulder.json'));assert d['active_work_id'] is None"` exit 0。
- 证据：`.omo/evidence/task-boulder-reset.txt` 记录修复前后关键字段 diff。

## TODOs

- [x] 1. 复位 boulder.json 状态机并补登记新计划
  What to do: 编辑 `.omo/boulder.json`：`active_work_id` → `null`；移除/清空顶层镜像字段（active_plan/plan_name/status/started_at/updated_at/agent/ended_at/elapsed_ms）；`works` 保留全部历史条目；新增 `works["dse-funcmodel-prefill-closure-<hash>"]` 条目（status=completed，active_plan 指向新计划文件，时间戳取 2026-08-13）。
  Acceptance criteria: `python3 -m json.tool .omo/boulder.json` 通过；`active_work_id` 为 null；works 中旧条目 count 不变 + 新增 1 条；`git status` 无意外文件。
  RED: 修复前 `python3 -c "...assert d['active_work_id'] is None"` 失败（当前指向旧 work_id）。
  GREEN: 修复后该断言 exit 0，json.tool exit 0。
  Mutation: 临时把 `active_work_id` 改回旧值，断言重新失败，再还原。
  Evidence: `.omo/evidence/task-boulder-reset.txt`

## Final Verification Wave

- [x] F1. State check
  What to do: 确认 boulder.json 复位后不再触发旧计划 nudge；确认新计划 `dse-funcmodel-prefill-closure` 的完成状态已归档在 works 中。
  Command: `python3 -m json.tool .omo/boulder.json && git status --short`

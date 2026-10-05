# DeepSeek Harness 兼容 Claude Code Mods —— 崔添翼亲自答的一手源核实与研究补记

> 日期：2026-10-04（归档 2026-10-05）
> 类型：外部事件一手源核实（用户分享的中文稿 → 知乎原答 + 官方仓库/兼容文档回溯）
> 触发：公众号「赛博禅心」2026-10-04 稿《DeepSeek Harness 为什么要兼容 Claude Code Mods》（正文 = 知乎回答全文引用）
> 完整归档：`~/Documents/Obsidian_Vault/阅读存档/2026-10-04-DeepSeekHarness兼容ClaudeCodeMods-崔添翼亲自答.md`

## 一、事实（一手源）

| 项 | 一手源口径 |
|---|---|
| 发布 | DSH **v0.2.1-alpha.1**，tag `dsh-v0.2.1-alpha.1`，2026-10-03T06:42Z；release 作者 **@tianyicui** |
| 新功能原文 | 「新增**实验性** Claude Code Mods 兼容层。目前阶段的主要目的是**验证 Claude Code Mods API 功能大致为 DeepSeek Harness 插件的一个子集**，而非为用户提供实际的完整兼容性。@tianyicui」 |
| 回答者 | 知乎「亲自答」：**崔添翼**，自称 DeepSeek Harness 组成员、兼容层作者；3,013 赞同 / 问题浏览 431,489；GitHub `tianyicui` 的 company = `@deepseek-ai` |
| 兼容文档 | `docs/subsystems/claude-code-mods.zh.md`（16,971 B，含英文版）——基准 = **Claude Code 2.1.287** + `claude.dev` 2026-10-01《Getting started with Claude Code mods》 |
| 仓库 | `deepseek-ai/deepseek-harness`：MIT，建于 2026-08-13，**243,574★ / 29,191 fork**（2026-10-05 读），描述 "Everything is a Plugin." |
| 相关 | Cordis = `cordiverse/cordis`，**9,009★ / MIT**，"Meta-Framework of Spatiotemporal Composability"；DSH 插件经 `cordis.yml` 挂载 |

## 二、兼容文档里比回答更关键的六条（桥接的真实代价）

1. **无沙箱、进程全权限**：钩子模块以 Node 全局对象**在进程内**运行，「没有访问规则」——`$.env` 读写环境变量、`$.http.fetch` 可访问任意 URL、`$.fs` 与 `$.tool.call` **以会话身份行动**。原文：「不对模组施加沙箱；只挂载你愿意作为插件运行的模组」。
2. **加载层级压平**：`user` / `project` / `managed` 三级 → 每个模组都按 `user` 加载；`next.to(e, tier)` 直接拒绝。
3. **无热重载、无校验工具**：没有 `claude plugin validate` / `plugin test`；重挂载在同一已求值模块上**重跑 `register`**，**模块级变量保留其值**。
4. **TS 支持有前提**：`.ts` 钩子模块只在**启动器转译**时可加载（源码启动可以，构建后安装不行）——DSH 交付纯 Node。
5. **清单不兼容**：不读 `plugin.json` / `hooks.json`；模组就是插件（`defineMod({ name, version, root, userConfig, register })`）；仓库外模组须导入 `@deepseek-ai/dsh-experimental-claude-code-mods`；`hooks.json` 里的设置钩子须另挂 `@deepseek-ai/dsh-hooks-claude-code`。
6. **静默风险有兜底**：永不触发的事件注册钩子会在**加载时警告点名**；服务表外的 `$` 调用以 `no implementation for <namespace>.<method>` **显式拒绝**。另暴露 `ctx.claudeCodeMods.watchBand/pressBand`（@Remote stream）——模组可绘制提示符上方横幅并接收按钮。

## 三、对我方（Hermes / CaduceusCore / 公众号）的启示

1. **「用对手的 API 当自己的能力探针」是可复用手法**：DSH 不做讨好式兼容，而是把 Mods API 当**能力清单**逐条对照自身插件面，缺失项写进文档。我方 `skill_impact.py` 只有自评置信度，**没有外部标准逐条打分机制**——可拿 Anthropic Skills / Claude Mods / MCP 的官方能力定义做探针，逐条标注「有 / 无 / 不同」。
2. **插件面越统一，权限模型越成为唯一安全边界**：DSH 把「无沙箱 + 进程全权限」**写进文档**（默认信任），与 Hermes 的 `agent-hooks`（审批 + 拦截，默认拦截）正相反。这与 10-01 ChipMEM 的领域验收门禁、09-29 Thariq 的 skills eval 是同一条判据：**能力越自主，验收与权限越要显式**。
3. **「创造模式」= 运行时自改 harness**，与 Hermes 的 `skill_self_evolution` 是同一命题的两个实现层；差距在**热插拔/热重载的工程完备度**（作者自承 Cordis 只提供基础，工程难题未解）。
4. **「训练模型写插件」才是真正的赌注**：回答落点是「以新的方式训练 LLM，让模型知道可以改变自身的 Harness」——主线命题「模型定上限、harness 定逼近度」在自进化语境下应是**相乘关系**，而非并列补偿。
5. 公众号选题：《兼容是幌子，探针才是目的》——素材 = §二 六条 + 我们自己的 hooks/skills 权限模型对照。

## 四、未核实 / 待补

- Claude Code Mods 官方 API 定义（`claude.dev` 2026-10-01 文 + `claude-code.d.ts`）本次未取（需代理），只读到 DSH 文档的转述层。
- 「行业 DSH 插件已实际部署」仅作者自述，无第三方材料。
- 招聘链接 `app.mokahr.com/su/12xvM6` 未验活。
- **已剔除的误推**：`tianyicui` **不是** Cordis 作者（cordiverse/cordis 贡献者 top5 = shigma 548 / Hieuzest 8 / undefined-moe 3 / justkyriecai 2 / morluto 2），其 65 个公开仓库无 cordis。

## 五、检索环境备注

- 知乎回答：curl 不可用，走 **CDP 浏览器**（`browser_exec` + `document.documentElement.innerText`，首次 `document.body` 为 null 需 reload + sleep 4s 再取）。
- GitHub（`api.github.com` / `raw.githubusercontent.com`）本机**直连可达**，release、tags、tag 内文档全部一次取到。

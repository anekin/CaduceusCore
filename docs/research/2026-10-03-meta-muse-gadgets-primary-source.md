# Meta 开源 Muse Gadgets —— 一手源核实与 Agent 硬件生态补记

> 日期：2026-10-03（归档 2026-10-05）
> 类型：外部事件一手源核实（用户分享的中文稿 → 官方仓库/新闻室回溯）
> 触发：公众号「Datawhale」2026-10-03 稿《刚刚，Muse迎来了一次大更新！》（其上游为机器之心同日 21:28 稿）
> 完整归档：`~/Documents/Obsidian_Vault/阅读存档/2026-10-03-Meta开源Muse-Gadgets-一手源核实.md`

## 一、事件事实（一手源）

| 项 | 一手源口径 |
|---|---|
| 发布 | Meta 于 **2026-10-02** 开源 Muse Gadgets |
| 仓库 | `github.com/facebookincubator/muse-gadget-sdk`，**Apache-2.0**，创建于 2026-10-02T18:21Z（首日 1,279★ / 236 fork） |
| 内容 | `esp32/`（ESP32 固件）+ `linux/`（Linux SDK）+ `skills/`（43 个设备技能）；共 584 条路径 |
| 配件 | **Muse Home Link**（USB-C 供电，接入家庭局域网，桥接 Muse 与既有智能家居）；**试产约 5,000 台**，面向美国 Muse 订阅用户免费发放（每人限 1 台，先到先得，本月发货） |
| 门槛 | 每个 gadget 必须持 **SDK token**（`gadgets.muse.ai/settings/sdk-tokens`）才能配对；另有 **Gadget SDK Terms**；**未承诺接口稳定性** |
| 官方措辞 | “Muse gadgets are open source devices you build yourself… Program an off-the-shelf ESP32 board or set up a Raspberry Pi with our SDKs” — “built by hackers, for hackers, just for fun” |
| 上游背景 | Muse 是 Meta 的**个人 AI Agent**（新闻室 2026-09-08《Introducing Muse》，跑在 Muse Secure VM 上）；此前 04 月 Muse Spark、07 月 Muse Image、09-28/29 企业版与小微企业版 |

**官方建议的三个项目（The Verge 2026-10-02 + 机器之心）**：① 彩色墨水屏（低功耗提醒器/看板）② **HDMI 电视棒**（投大屏）③ 小型触控设备（自制「Muse Charm」造型）。

**板卡实证（`esp32/devices/`，26 个 sdkconfig）**：ESP32-C5 DevKitC-1 / ESP32-C6（无 PSRAM）/ ESP32-S3-DevKitC-1 / Waveshare ESP32-C6-LCD-1.47、**ESP32-S3-Touch-AMOLED-1.75 与 1.75C**、C6-Touch-AMOLED-1.8 / Seeed SenseCAP Indicator、SenseCAP Watcher、**reTerminal E1001（7.5" 黑白）、E1002（7.3" 六色）** / Home Assistant Voice PE / M5Stack StickC Plus2、StickS3、Cardputer ADV、Core2、CoreS3、Stopwatch / ESP32-S3-BOX-3 / AIPI Lite / ideaspark 等。带 `UI` 标记的板子可跑完整屏上 UI（动画形象 + push-to-talk + 设置）。

**Linux SDK 权限边界（README 原文）**：Raspberry Pi 3B+/4/5/Zero 2W 或任何带 Bluetooth LE 的 Linux；**需要 sudo 账号**，“Muse gets the same access to the machine as the account you install it for”。

## 二、中文稿（Datawhale）四处口径问题

| # | 中文稿写法 | 一手源实际 | 性质 |
|---|---|---|---|
| 1 | 「官方推荐的**四种**硬件形态」 | 一手源是**三个建议项目**；「四种形态」为中文稿自造分类（板卡型号本身属实，但第 ③ 项「触控掌上挂件」被替换成具体板卡） | 框架改写 |
| 2 | 「Muse 应用在**上线 13 天**内便登顶美区 App Store 免费榜」 | Sensor Tower：第 5 天 73 万下载，**第 10 天登顶**，**第 13 天**累计 250 万（iOS ~150 万 / 安卓 ~110 万）；Apptopia 口径 12 天 ~280 万 | 窗口混淆（两机构口径也不可混用） |
| 3 | 「8.2 万关注者」「Connector 平台超 3000 项提交」 | **未找到一手来源**（X 不可达、仓库/新闻室/The Verge 均未见） | 未核实 |
| 4 | 只写「推出一款官方配件 Muse Home Link」 | **漏掉本轮最出圈信息**：试产 5,000 台、美国订阅用户免费发放 | 重大遗漏 |

另两处细节缺失：Viticci 改造的是 **Xteink 墨水瓶**（Muse 形象名 Calliope）；「50 美元开发板」实为 **Seeed SenseCAP Watcher**，经 **Tailscale 连接器**同步酒窖。

## 三、对我方（Hermes / CaduceusCore / 公众号）的启示

1. **Agent Skills 的第三种形态出现**：`skills/` 里是 43 个「设备技能」（42 设备 + Google Cast，覆盖 HomePod/Apple TV/Philips Hue/Roborock/Dyson/Miele 等）。同一份 skill 概念，验收标准变成「灯能不能真的亮、文件能不能真的送到打印机」——不是「模型觉得这个 skill 写得好」。这与 2026-09-29 Thariq 访谈（Anthropic 给 Skills 加 eval 插件）、2026-10-01 ChipMEM（产物过领域验收才入库）构成**第三条独立证据**：**skill 的有效性判据正在从「文本质量」转向「动作在真实环境中的结果」**。
2. **harness 命题的物理版**：Meta 出 Agent 与协议、第三方出载体，智能与载体解耦。这正是「模型定上限、harness 定逼近度」在硬件层的镜像——上限（模型）被固定，逼近度（载体/接口/技能）交给生态竞速。
3. **接口承诺的缺位是风险点，但和 Humane AI Pin 的失败不是一个问题**：前者是「不锁硬件、不承诺接口」，后者是「锁定硬件形态替代手机」。评估第三方硬件生态时应分开归因，不要混用旧案例下结论。
4. **对 CaduceusCore 的直接映射**：Linux SDK 把「与安装账号同权限」写进 README 作为**显式代价声明**，而不是藏在条款里——与我们 `estimated_cycles` / `calibration_state=uncalibrated` 的措辞纪律同源：**能力边界必须写在能力旁边**。
5. **公众号选题**：《Agent 长出身体之后，验收标准变了》——从「skill 写得好不好」到「动作有没有真的发生在物理世界」，可搭 09-29 Thariq + 10-01 ChipMEM + 本篇（43 个设备技能）。

## 四、检索环境备注（本次踩坑，供下次复用）

- Clash 订阅 2026-10-02 到期，仍为直连模式：`api.github.com` / `raw.githubusercontent.com` / `about.fb.com` / `theverge.com` / `baijiahao.baidu.com`（机器之心稿的百家号镜像）**全部直连 200**，无需代理。
- **`gadgets.muse.ai` 不可达**：CNAME 到 `star.c10r.facebook.com`（174.132.167.252），curl 超时、浏览器被安全层判为私网地址拦截 → 官方产品页（含「官方推荐硬件形态」原文）本次**未能读取**，只能以 The Verge + 机器之心交叉推定。
- **X 帖不可达**：Alexandr Wang / viticci / natfriedman 的原始帖均未一手核实，仅存链接。后续若恢复代理，优先补核这三条。
- 中文稿的上游常被隐藏：Datawhale 稿（10-03 23:33）比机器之心稿（10-03 21:28）晚 2 小时且内容同构 → **先找中文上游，再找英文一手**是本次最快路径。

# 🦬 Robinhood Memescan

[English](README.md) · **中文**

一个完全基于 **[Clawby](https://www.openclawby.com/) API** 的 **meme 币实时监控 + AI 评分看板**。七个监控窗口 + 一个自定义报告窗口 + 一个管理面板。监控 Robinhood 发射、热门 meme 排名,并对 **Robinhood、BSC、Base 三链做自动化 AI 评分**,还能一键生成**含图表的 PDF 研究报告**——全部由本地 **Claude Code / Codex** 驱动,可选 Telegram 提醒。

> 数据全部来自 Clawby(`dex_trending` / `dex_token_info` / `dex_token_holders` / `dex_token_kline` / `dex_token_traders` / `dexscreener_*` / `x_search` + 链上 RPC),无需 Blockscout。

---

## 功能一览

| 窗口 | 说明 |
|---|---|
| **① 新发射** | 轮询各发射平台的工厂合约事件(Clawby RPC `eth_getLogs`)发现新币:平台 / 名称 / CA / 创建时间 / 创建者 / 创建者是否 KOL + 创建者在 **5 条链**(robinhood · ETH · BSC · Base · HyperEVM)的交易笔数 |
| **② Top100** | 按 24h 交易量排名前 100:平台 / 价格 / 市值 / 24h量 / holders / **交易数(24h swaps)** / 流动性 / 池内 ETH / X 舆情 |
| **③ 收藏** | 收藏任意 CA,按自定义频率刷新;额外显示 **KOL / 聪明钱数**(采样 top 持有人),一键 **🔬 深度分析 → PDF** |
| **④ 3天内新币** | 只看**真实链上创建时间 ≤ 3 天**的币,按交易量取前 100(创建时间经链上校准,排除"刚毕业的老币") |
| **⑤ AI·Robinhood** | 每 10 分钟扫描**可自定义市值区间**(默认 $100k–$5M,可加"创建 ≤ N 天"筛选)的 meme,把链上 / 持有人 / 交易 / 社交数据存成 LLM wiki,再用 **Claude Code / Codex 评 1–100 分**(100=最值得早期买入)+ **1–5 句理由** + **24h 交易数**。可选**评分 ≥ 阈值时 Telegram 提醒** |
| **⑥ AI·BSC** | 对 **BSC** meme 做同样的自动化 AI 评分(独立市值区间/年龄筛选) |
| **⑦ AI·Base** | 对 **Base** meme 做同样的自动化 AI 评分(独立市值区间/年龄筛选) |
| **⑧ 自定义报告** | 选链(Robinhood/BSC/Base)+ 粘贴合约 → Clawby 抓取**全部 RPC / 持有人 / 交易 / 持有者关联 / X 数据** → 生成含**价格折线图、成交量柱、持有人分布环图、时间线、总TX**的完整 **PDF** |
| **⚙️ 管理** | 全部设置:并发、报告目录、报告语言、**分析引擎**、各窗口间隔+暂停、**各评分器市值区间+年龄筛选**、发射平台开关、**扫描字段自定义**、Telegram 提醒、运行状态与最近错误 |

**其它:**
- **中英双语界面** —— 首次打开选语言,整套界面即时切换。
- **合约/地址一键复制**(`⧉` → ✓ + 提示)。
- **实时反馈** —— 每个窗口状态药丸(扫描中 / 空闲 / 已暂停 / 评分中)、AI 评分进度条、当前正在评分哪个币、顶栏活动指示。
- **本地持久化** —— 收藏和所有设置存在 `state.json`,重启自动恢复。
- **启动保护** —— 窗口默认**暂停**启动(避免并发骤增),且**最多同时监控 2 个**。

---

## 环境要求

- **Python 3.9+**(自动创建 venv 并安装 `fastapi` / `uvicorn` / `httpx`)
- **Clawby API Key** —— 到 <https://www.openclawby.com/> 免费注册(`pk_` 开头)
- **深度分析 / AI 评分(可选):**
  - **Google Chrome**(macOS)将报告 HTML 渲染成 PDF
  - **Claude Code CLI**(`claude`,已登录)**或** **Codex CLI**(`codex login` / `OPENAI_API_KEY`)
- **Telegram 提醒(可选):** 一个 Telegram bot token(找 [@BotFather](https://t.me/BotFather) 申请)

> 只做监控的话,Python + Clawby Key 即可;AI/PDF 和 Telegram 都是可选项。

---

## 安装与运行

```bash
cd Robinhood_Memescan

# 1. 配置 Key
cp .env.example .env
#    编辑 .env → 填 CLAWBY_API_KEY=pk_xxx

# 2. 运行(首次会自动建 venv、装依赖)
bash run.sh
```

然后打开 **<http://127.0.0.1:8799>** 并选择语言。

`.env`:
```ini
CLAWBY_API_KEY=pk_your_key_here   # 必填,来自 openclawby.com
PORT=8799                          # 可选,默认 8799
```

其余设置都在**管理**窗口配置(且会持久化):

| 设置 | 说明 |
|---|---|
| 并发请求数 | 1–100,默认 10(配合内置 ~6 req/s 限流) |
| 报告输出目录 | 分析 PDF 的保存位置,默认 `./reports` |
| 报告语言 | 中文 / English |
| **分析引擎** | **Claude Code** 或 **Codex** |
| 各窗口扫描间隔 | ① 5s · ② / ④ 300s · ⑤⑥⑦ 600s |
| **各评分器市值区间+年龄** | ⑤⑥⑦ 各自独立设置市值 min/max 和"创建 ≤ N 天"筛选 |
| 发射平台工厂开关 | 启用/停用窗口①监听的平台 |
| 扫描字段自定义 | 关掉某字段即跳过其抓取(省配额) |
| Telegram 提醒 | token / chat_id / 阈值 / 开关 |

---

## AI 评分(⑤ Robinhood · ⑥ BSC · ⑦ Base)

三个自动化的"早期投资价值"评分器,每链一个:

1. 每 **10 分钟**用 `dex_trending` 筛出该链**配置的市值区间**内的 meme(默认 $100k–$5M,还可要求"创建 ≤ N 天")。
2. 一个**顺序后台 worker** 逐个处理:采集数据 → 存成 LLM wiki(`scores/<chain>_<ca>/wiki/`)→ 调**快模型 Claude/Codex** 读 wiki 打分。
3. 输出 **1–100 分**(100=最值得早期买入,1=风险最高)+ **1–5 句理由** + 该币 **24h 交易数**。
4. 分数**持久化**并实时更新;新币/最久没评的优先,之后持续滚动复评。

> 几十个币无法在同一个 10 分钟内全部重评(每个都是独立 LLM 调用),所以是"数据每 10 分钟刷新 + 持续滚动评分"。引擎沿用管理窗口的 Claude/Codex 设置。因"最多同时监控 2 个窗口",同时开 ⑤+⑥ 就能并行跑 Robinhood 和 BSC 评分。

## 自定义深度报告(⑧)

选链 + 粘贴合约地址 → app 抓取该币的**全部数据**并让 AI 写一份含图表的完整报告:

- 链上:总TX(RPC 从部署块统计)、24h 交易数、去重发送/接收地址、部署块
- 持有人:完整 top 持有人表 + **分布环图**,以及**关联信号**(top10 集中度、sniper / bundler / 新钱包 / dev 持仓率)
- 交易:按已实现盈亏的 top traders;由 OHLC 生成的**价格折线图** + **成交量柱**;事件**时间线**
- 社交:近期 X 讨论

图表由服务端预生成(Chart.js,内置 `vendor/chart.min.js`,渲染时不联网),再由 Chrome 渲染成**多页 PDF**(完整报告约 5–7 分钟,窗口有实时进度)。

## Telegram 提醒

评分 **≥ 阈值**时推送提醒。管理窗口 → **📲 Telegram**:

1. 先在 Telegram 里给你的 bot 发一条消息(bot 才能拿到你的 chat id)。
2. 填 **Bot Token** → 点 **刷新 chat_id** 自动带出你的 chat → 选中。
3. 设 **阈值**(默认 80)、启用、点 **发送测试**。
4. 之后每个 ≥ 阈值的评分都会推送(同一币只推一次;跌破阈值后回升会再推)。

> Token 可随时替换(显示时掩码)。Telegram 通常需要代理——app 会复用 `run.sh` 保存的代理并对偶发断连重试。

## 扫描字段自定义

管理窗口 → **扫描字段自定义** 可逐窗口开关字段。**关掉字段 = 跳过其抓取(省 Clawby 配额、更快)+ 隐藏该列。** 例如关掉 X 舆情,所有窗口就不再调 `x_search`。

## 深度分析(🔬)

在收藏窗口点某币的 **🔬 分析**:抓取其链上 + X 数据 → 本地 wiki → 本地 **Claude Code / Codex** 读取并写出 HTML 报告 → Chrome 渲染成 **PDF** 存到报告目录。

---

## 数据来源(全部经 Clawby)

**`/api/relay`:** `dex_trending`(榜单,robinhood/bsc/base 任一链)· `dex_token_info`(单币快照:holders / 价格 / 量 / 创建时间 / 平台 / creator / 关联信号)· `dex_token_holders` · `dex_token_kline`(K线,图表用)· `dex_token_traders`(top traders)· `dexscreener_token_pools`(池流动性 / ETH)· `dex_wallet_stats`(钱包 KOL / 聪明钱标签)· `x_search`(X 舆情)

**`/api/rpc`(`chain` = robinhood / bsc / base):** `eth_blockNumber` · `eth_getLogs`(工厂事件 / 转账计数)· `eth_call` · `eth_getCode`(二分查部署块)· `eth_getBlockByNumber` · `eth_getTransactionByHash` · `eth_getTransactionCount`

**唯一非 Clawby 调用:** HyperEVM 公共 RPC(`eth_getTransactionCount`,因 Clawby 无 hyperevm 链)。

Clawby 背后的提供方:**GMGN**(`dex_*`,覆盖 robinhood/bsc/base)、**DexScreener**(`dexscreener_*`)、**X**(`x_search`)——各自有独立的上游限流。

---

## 项目结构

```
Robinhood_Memescan/
├── app.py          FastAPI:REST 端点 + 状态持久化 + 生命周期
├── monitors.py     窗口逻辑(发现 / 榜单 / 收藏 / 多链 AI 评分器)+ 循环 + 控制
├── sources.py      Clawby 数据封装(rh_trending / rh_token / rh_holders / rh_by_mcap / rh_kline / rh_traders),多链
├── clawby.py       Clawby 客户端:relay + rpc + 并发 + 限流 + 封禁退避
├── analyze.py      AI 评分 + 深度/自定义报告(采集 → wiki → 图表 → claude/codex → PDF)
├── tg.py           Telegram 提醒
├── wallets.py      钱包 KOL / 聪明钱打标签 + 缓存
├── factories.py    发射平台工厂地址 + 事件解码规则
├── util.py         共用工具 + 缓存 + 限流器 + 状态读写
├── dashboard.html  前端(原生 JS 单页,7 窗口 + 报告 + 管理,i18n)
├── vendor/chart.min.js   内置 Chart.js,报告图表用(渲染时不联网)
├── run.sh          venv / 依赖 / 代理处理 / uvicorn
├── .env.example    配置模板
└── .env            你的实际配置(不入库)
```

---

## 说明与限制

- **Robinhood 链** = Arbitrum Orbit L2,chain id 4663。
- **代理:** `run.sh` 启动时清除 http(s)/all 代理(app 的 httpx 必须直连),但把它存到 `SAVED_*_PROXY` 供 `claude` / `codex` 子进程和 Telegram 使用。
- **首屏:** ② / ④ 冷启动需 ~1–2 分钟补全(受限流约束),窗口会显示进度,不是卡死。
- **上游限流:** 高强度突发(反复重启、密集轮询)可能让某个提供方(通常是 GMGN)临时按 IP 限流封禁。客户端会识别(按响应体,任意 HTTP 状态)、以递增冷却退避、显示"上游限流,剩 Xs"横幅并自动恢复。正常开 2 个窗口(~1 请求/秒)远低于限流。
- **KOL / 聪明钱**依赖 GMGN 钱包标签;某币的 top 持有人常是池子/大户,所以数量经常是 0。
- 报告 / 分析 / 评分目录只保留最新的若干条。

*本工具仅供研究,不构成投资建议。*

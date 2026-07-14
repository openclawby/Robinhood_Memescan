# 🦬 Robinhood Memescan

基于 **Clawby API** 的 Robinhood 链 meme 币实时监控与深度分析看板。四个监控窗口 + 一个管理面板，一键抓取链上/社交数据并用本地 **Claude Code / Codex** 生成 PDF 研究报告。

> 数据全部来自 Clawby（`dex_trending` / `dex_token_info` / `dex_token_holders` / `dexscreener` / `x_search` / 链上 RPC）。无需 Blockscout。

---

## 功能一览

| 窗口 | 说明 |
|---|---|
| **① 新发射实时** | 轮询各发射平台的工厂合约事件（Clawby RPC `eth_getLogs`），实时发现新币：平台 / 名称 / CA / 创建时间 / 创建者 / 创建者是否 KOL / 创建者在 robinhood·ETH·BSC·Base·HyperEVM 五链的交易笔数 |
| **② Top100 热门** | 按 24h 交易量排名的前 100 meme：平台 / 价格 / 市值 / 24h量 / holders / **交易数(24h swaps)** / 流动性 / 池内 ETH / X 舆情 |
| **③ 收藏 · 深度分析** | 收藏任意 CA，按自定义频率刷新；额外显示 **KOL 数 / 聪明钱数**（采样 top 持有人打标签）；一键 **🔬 深度分析** 生成 PDF |
| **④ 3天内新币** | 只看**真实创建时间 ≤ 3 天**的币，按 24h 量取前 100（自动用链上创建时间校准，排除"老币刚毕业"） |
| **⑤ 管理** | 全局设置（并发 / 报告目录 / 报告语言 / **分析引擎** ）、各窗口扫描间隔与暂停、发射平台工厂开关、运行状态与最近错误 |

**其它：**
- **合约/地址一键复制** —— 所有 CA、创建者地址旁都有 `⧉` 复制按钮，点击后按钮变 ✓ 并弹出"已复制"提示。
- **实时反馈** —— 每个窗口有状态药丸（🟢 扫描中·补全 45/100 / ✓ 空闲 / ⏸ 已暂停），顶栏显示"⏳ 扫描中 ②④"。
- **状态持久化** —— 收藏、平台开关、并发、语言、引擎、扫描间隔都存到 `state.json`，重启自动恢复（窗口启动一律开启）。

---

## 环境要求

- **Python 3.9+**（自动创建 venv，装 `fastapi` / `uvicorn` / `httpx`）
- **Clawby API Key** —— 到 <https://www.openclawby.com/> 注册获取（`pk_` 开头）
- **深度分析（可选）：**
  - **Google Chrome**（macOS，用于把 HTML 报告渲染成 PDF）—— 路径默认 `/Applications/Google Chrome.app`
  - **Claude Code CLI** (`claude`) —— 需已登录；或
  - **Codex CLI** (`codex`) —— 需已登录（`codex login`）或设置 `OPENAI_API_KEY`

> 不做深度分析的话，只需 Python + Clawby Key 即可跑监控。

---

## 安装与配置

```bash
# 1. 进入目录
cd Robinhood_Memescan

# 2. 配置 API Key：复制模板并填入你的 Clawby Key
cp .env.example .env
#   编辑 .env，把 CLAWBY_API_KEY 换成你自己的 pk_xxx

# 3. 启动（首次会自动建 venv、装依赖）
bash run.sh
```

启动后打开 **<http://127.0.0.1:8799>**。

`.env` 配置项：

```ini
CLAWBY_API_KEY=pk_your_key_here   # 必填，来自 openclawby.com
PORT=8799                          # 可选，默认 8799
```

其余设置在**管理窗口**里改（都会持久化）：

| 设置 | 说明 |
|---|---|
| 并发请求数 | 1–100，默认 10（配合内置 6 req/s 限流，避免打爆 Clawby 配额） |
| 报告输出目录 | 深度分析 PDF 的保存位置，默认 `./reports` |
| 分析报告语言 | 中文 / English |
| **🔬 深度分析引擎** | **Claude Code** 或 **Codex** |
| 各窗口扫描间隔 | ① 默认 5s、② / ④ 默认 300s |
| 发射平台工厂开关 | 逐个启用/停用要监听的发射平台 |

---

## 深度分析（🔬）

在 **③ 收藏**窗口点某个币的"🔬 分析"：

1. **抓取** —— Clawby 拉取该 CA 的链上数据（token 信息、创建者画像、持有人标签、转账活动、DEX 池）+ 当日 X 讨论；
2. **本地 wiki** —— 汇总成 markdown 存到 `analysis/<ca>/wiki/`；
3. **AI 分析** —— 调用本地 **Claude Code** 或 **Codex**（在管理窗口切换）读取 wiki，产出结构化 HTML 研究报告（快照 / 链上 / 持有人 / 舆情 / 风险 / 结论）；
4. **PDF** —— Chrome 无头模式渲染成 PDF，存到报告目录。

> 引擎切换：管理窗口 → "🔬 深度分析引擎" 选 Claude Code 或 Codex。两者需各自登录（`claude` 已登录 / `codex login`）。分析中窗口会显示"本地 claude 分析中"或"本地 codex 分析中"。

---

## 数据来源（全部经 Clawby）

**`/api/relay` 数据接口：**
`dex_trending`（榜单）· `dex_token_info`（单币全量：holders/价格/量/创建时间/平台/creator）· `dex_token_holders`（持有人榜）· `dexscreener_token_pools`（池流动性/ETH）· `dex_wallet_stats`（钱包 KOL/聪明钱标签）· `x_search`（X 舆情）

**`/api/rpc` 链上（`chain=robinhood`）：**
`eth_blockNumber` · `eth_getLogs`（工厂事件/转账）· `eth_call`（name/symbol）· `eth_getBlockByNumber` · `eth_getTransactionByHash` · `eth_getTransactionCount`（多链笔数）

**唯一非 Clawby 调用：** HyperEVM 公共 RPC（`eth_getTransactionCount`，因 Clawby 无 hyperevm 链）。

---

## 项目结构

```
Robinhood_Memescan/
├── app.py          FastAPI 入口：REST 端点 + 状态持久化 + 生命周期
├── monitors.py     四窗口逻辑（W1 发现 / W2·W4 榜单 / W3 收藏）+ 循环 + 控制
├── sources.py      Clawby 数据封装（rh_trending / rh_token / rh_holders）
├── clawby.py       Clawby 客户端：relay + rpc + 并发信号量 + 限流
├── wallets.py      钱包 KOL/聪明钱打标签 + 缓存
├── factories.py    各发射平台工厂地址 + 事件解码规则
├── analyze.py      深度分析流水线（gather → wiki → claude/codex → PDF）
├── util.py         共用工具 + 缓存 + 限流器 + 状态读写
├── dashboard.html  前端（原生 JS 单页，四 Tab + 管理）
├── run.sh          建 venv / 装依赖 / 处理代理 / 起 uvicorn
├── .env.example    配置模板
└── .env            你的实际配置（不入库）
```

---

## 说明与限制

- **Robinhood 链** = Arbitrum Orbit L2，chain id 4663。
- **代理**：`run.sh` 会在启动时清除 http(s)/all 代理（app 的 httpx 必须无代理直连），同时把原代理存到 `SAVED_*_PROXY`，供 `claude`/`codex` 子进程使用。
- **首屏**：W2/W4 冷启动需 ~1–2 分钟补全（受 6 req/s 限流约束），窗口会显示进度，不是卡死。
- **KOL/聪明钱**：靠 GMGN 钱包标签，某币 top 持有人常是池子/大户，命中率天然偏低（常为 0）。
- **缓存/清理**：报告与分析目录仅保留最新 25 份。

*本工具仅供研究，不构成投资建议。*

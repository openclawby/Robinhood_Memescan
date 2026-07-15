# 🦬 Robinhood Memescan

**English** · [中文](README.zh-CN.md)

A real-time **meme-coin monitor & AI scoring dashboard**, built entirely on the **[Clawby](https://www.openclawby.com/) API**. Seven monitor windows + a custom-report window + an admin panel. It watches Robinhood launches, ranks the hottest memes, and runs **automated AI scoring on Robinhood, BSC and Base**, plus one-click **illustrated PDF research reports** — all powered by your local **Claude Code / Codex**, with optional Telegram alerts.

> All data comes from Clawby (`dex_trending` / `dex_token_info` / `dex_token_holders` / `dex_token_kline` / `dex_token_traders` / `dexscreener_*` / `x_search` + on-chain RPC). No Blockscout needed.

---

## Features

| Window | What it does |
|---|---|
| **① New Launches** | Polls each launchpad's factory events (Clawby RPC `eth_getLogs`) for brand-new tokens: platform / name / CA / creation time / creator / is-creator-a-KOL + the creator's tx counts across **5 chains** (robinhood · ETH · BSC · Base · HyperEVM) |
| **② Top100** | Top 100 memes by 24h volume: platform / price / mcap / 24h vol / holders / **txns (24h swaps)** / liquidity / ETH-in-pool / X buzz |
| **③ Watchlist** | Favorite any CA, per-CA refresh interval; adds **KOL / smart-money counts** (sampled top holders) and a one-click **🔬 deep analysis → PDF** |
| **④ New ≤3 days** | Only coins whose **real on-chain creation time ≤ 3 days**, top 100 by volume (creation time is calibrated on-chain, so "old coins that just graduated" are excluded) |
| **⑤ AI · Robinhood** | Every 10 min scans memes in a **configurable market-cap band** (default $100k–$5M, optional "created ≤ N days" filter), stores each one's on-chain / holder / trading / social data as an LLM wiki, then uses **Claude Code / Codex to score it 1–100** (100 = best early buy) with a **1–5 sentence rationale** + its **24h txn count**. Optional **Telegram alert** when a score clears a threshold |
| **⑥ AI · BSC** | The same automated AI scoring for **BSC** memecoins (own mcap band / age filter) |
| **⑦ AI · Base** | The same automated AI scoring for **Base** memecoins (own mcap band / age filter) |
| **⑧ Custom Report** | Pick a chain (Robinhood / BSC / Base) + paste a contract → Clawby gathers **all RPC / holder / trade / holder-relationship / X data** → generates a full **illustrated PDF** with a price line chart, volume bars, a holder-distribution doughnut, a timeline and the total tx count |
| **⚙️ Admin** | Every setting: concurrency, report dir, report language, **analysis engine**, per-window interval + pause, **per-scorer mcap band + age filter**, launchpad toggles, **scan-field customization**, Telegram alerts, runtime status & recent errors |

**Also:**
- **Bilingual UI (English / 中文)** — a language picker on first open; the whole interface switches instantly.
- **Copy buttons** on every contract / address (`⧉` → ✓ + toast).
- **Live feedback** — per-window status pills (scanning / idle / paused / scoring), an AI-score progress bar, the coin being scored right now, and a header activity indicator.
- **Local persistence** — favorites and every setting live in `state.json` and are restored on restart.
- **Startup safety** — windows boot **paused** (no request spike) and **at most 2 can monitor at once**.

---

## Requirements

- **Python 3.9+** (a venv with `fastapi` / `uvicorn` / `httpx` is created automatically)
- **Clawby API key** — sign up (free) at <https://www.openclawby.com/> (starts with `pk_`)
- **For deep analysis / AI scoring (optional):**
  - **Google Chrome** (macOS) to render report HTML → PDF
  - **Claude Code CLI** (`claude`, logged in) **or** **Codex CLI** (`codex login` / `OPENAI_API_KEY`)
- **For Telegram alerts (optional):** a Telegram bot token (from [@BotFather](https://t.me/BotFather))

> Monitoring only needs Python + a Clawby key. The AI/PDF and Telegram pieces are opt-in.

---

## Install & run

```bash
cd Robinhood_Memescan

# 1. configure your key
cp .env.example .env
#    edit .env → set CLAWBY_API_KEY=pk_xxx

# 2. run (first run creates the venv + installs deps)
bash run.sh
```

Then open **<http://127.0.0.1:8799>** and pick a language.

`.env`:
```ini
CLAWBY_API_KEY=pk_your_key_here   # required, from openclawby.com
PORT=8799                          # optional, default 8799
```

Everything else is configured in the **Admin** window (and persisted):

| Setting | Notes |
|---|---|
| Concurrency | 1–100, default 10 (paired with a built-in ~6 req/s limiter) |
| Report output dir | where analysis PDFs are saved, default `./reports` |
| Report language | 中文 / English |
| **Analysis engine** | **Claude Code** or **Codex** |
| Per-window scan interval | ① 5s · ② / ④ 300s · ⑤⑥⑦ 600s |
| **Per-scorer mcap band + age** | ⑤⑥⑦ each get their own market-cap min/max and "created ≤ N days" filter |
| Launchpad factory toggles | enable/disable the platforms window ① listens to |
| Scan-field customization | turn individual fields off to skip their fetch (saves quota) |
| Telegram alerts | token / chat_id / threshold / on-off |

---

## AI scoring (⑤ Robinhood · ⑥ BSC · ⑦ Base)

Three automated "early-stage opportunity" scorers, one per chain:

1. Every **10 min**, `dex_trending` selects that chain's memes in the configured **market-cap band** (default $100k–$5M; you can also require "created ≤ N days").
2. A **sequential background worker** processes them **one at a time**: gather data → store as an LLM wiki (`scores/<chain>_<ca>/wiki/`) → call the **fast Claude/Codex model** to read the wiki and score.
3. Output = **1–100** (100 = strongest early buy; 1 = highest risk) + a **1–5 sentence rationale** + the coin's **24h txn count**.
4. Scores **persist** and update live; new / oldest-scored coins go first, then it keeps rolling.

> Dozens of coins can't all be re-scored within each 10-min tick (each is a separate LLM call), so it's "refresh data every 10 min + keep rolling through scoring". The engine follows the Admin Claude/Codex setting. Only 2 windows monitor at once, so opening ⑤ + ⑥ (say) runs Robinhood and BSC scoring in parallel.

## Custom deep report (⑧)

Pick a chain + paste a contract address → the app gathers **everything** for that token and asks the AI to write a full illustrated report:

- On-chain: total tx (RPC-counted from the deploy block), 24h tx, unique senders/receivers, deploy block
- Holders: full top-holder table + a **distribution doughnut**, plus **relationship signals** (top-10 concentration, sniper / bundler / fresh-wallet / dev-hold rates)
- Trading: top traders by realized PnL; a **price line chart** + **volume bars** from the OHLC series; an event **timeline**
- Social: recent X posts

The charts are pre-built server-side (Chart.js, bundled `vendor/chart.min.js` — no CDN), then Chrome renders it to a **multi-page PDF** (~5–7 min for a full report; the window shows live progress).

## Telegram alerts

Push an alert whenever a coin scores **≥ a threshold**. In Admin → **📲 Telegram**:

1. Message your bot once in Telegram (so it can see your chat id).
2. Paste the **Bot Token** → click **Refresh chat_id** to auto-detect your chat → select it.
3. Set the **threshold** (default 80), enable it, click **Send test**.
4. Afterwards every ≥-threshold score is pushed (once per coin; re-armed if it drops below then climbs back).

> The token can be replaced anytime (shown masked). Telegram usually needs a proxy — the app reuses the proxy `run.sh` saved and retries transient drops.

## Scan-field customization

Admin → **Scan-field customization** toggles fields per window. **Turning a field off skips its fetch (saves Clawby quota, runs faster) and hides its column.** E.g. turn off "X buzz" and no window calls `x_search` anymore.

## Deep analysis (🔬)

From the Watchlist, click a coin's **🔬 Analyze**: gather its on-chain + X data → local wiki → local **Claude Code / Codex** reads it and writes an HTML report → Chrome renders it to a **PDF** in the report folder.

---

## Data sources (all via Clawby)

**`/api/relay`:** `dex_trending` (ranking, any of robinhood/bsc/base) · `dex_token_info` (per-CA snapshot: holders / price / vol / creation time / launchpad / creator / relationship stats) · `dex_token_holders` · `dex_token_kline` (OHLC for charts) · `dex_token_traders` (top traders) · `dexscreener_token_pools` (pool liquidity / ETH) · `dex_wallet_stats` (wallet KOL / smart-money tags) · `x_search` (X sentiment)

**`/api/rpc` (`chain` = robinhood / bsc / base):** `eth_blockNumber` · `eth_getLogs` (factory events / transfer counts) · `eth_call` · `eth_getCode` (binary-search the deploy block) · `eth_getBlockByNumber` · `eth_getTransactionByHash` · `eth_getTransactionCount`

**Only non-Clawby call:** HyperEVM public RPC (`eth_getTransactionCount`, since Clawby has no hyperevm chain).

Providers behind Clawby: **GMGN** (`dex_*`, covers robinhood/bsc/base), **DexScreener** (`dexscreener_*`), **X** (`x_search`) — each has its own upstream rate limits.

---

## Project structure

```
Robinhood_Memescan/
├── app.py          FastAPI app: REST endpoints + state persistence + lifespan
├── monitors.py     window logic (discover / rank / watch / multi-chain AI scorers) + loops + controls
├── sources.py      Clawby data wrappers (rh_trending / rh_token / rh_holders / rh_by_mcap / rh_kline / rh_traders), chain-aware
├── clawby.py       Clawby client: relay + rpc + concurrency + rate limiter + ban backoff
├── analyze.py      AI scoring + deep/custom reports (gather → wiki → charts → claude/codex → PDF)
├── tg.py           Telegram alerts
├── wallets.py      wallet KOL / smart-money tagging + cache
├── factories.py    launchpad factory addresses + event decode rules
├── util.py         shared helpers + caches + rate limiter + state I/O
├── dashboard.html  frontend (vanilla JS single page, 7 windows + report + admin, i18n)
├── vendor/chart.min.js   bundled Chart.js for the report charts (no CDN at render time)
├── run.sh          venv / deps / proxy handling / uvicorn
├── .env.example    config template
└── .env            your real config (git-ignored)
```

---

## Notes & limits

- **Robinhood chain** = Arbitrum Orbit L2, chain id 4663.
- **Proxy:** `run.sh` clears http(s)/all proxy at startup (the app's httpx must connect directly) but saves it to `SAVED_*_PROXY` for the `claude` / `codex` subprocesses and Telegram.
- **First load:** ② / ④ take ~1–2 min to enrich on cold start (bounded by the rate limiter); the window shows progress, it isn't stuck.
- **Upstream rate limits:** heavy bursts (many restarts, aggressive polling) can get a provider (usually GMGN) to temporarily rate-limit-ban the IP. The client detects it (by response body, any HTTP status), backs off with an escalating cooldown, shows a "rate-limited, resets in Xs" banner, and auto-recovers. Normal 2-window use (~1 req/s) stays well under the limits.
- **KOL / smart-money** relies on GMGN wallet tags; a coin's top holders are often pools / whales, so counts are frequently 0.
- Reports / analysis / score dirs are pruned to the newest entries.

*This tool is for research only — not investment advice.*

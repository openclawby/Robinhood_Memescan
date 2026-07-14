"""Deep-analysis pipeline for a CA.

  gather (Clawby on-chain + X)  ->  local LLM wiki (markdown)
  ->  local `claude` CLI reads the wiki and writes report.html
  ->  Chrome headless renders report.html to a PDF in the report folder.
"""
import asyncio
import json
import os
import shutil
import time

import clawby
import sources
import util
import wallets

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_DIR = os.path.join(HERE, "analysis")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

REPORT_DIR = os.path.join(HERE, "reports")   # output folder (configurable)
ENGINE_TIMEOUT = 600

JOBS = {}   # ca -> {status, step, started, finished, report, error}

LANG = "zh"       # report language: zh | en
ENGINE = "claude"  # analysis engine: claude | codex


def set_lang(x):
    global LANG
    if x in ("zh", "en"):
        LANG = x


def get_lang():
    return LANG


def set_engine(x):
    global ENGINE
    if x in ("claude", "codex"):
        ENGINE = x


def get_engine():
    return ENGINE


def _prompt():
    langname = "简体中文 (Simplified Chinese)" if LANG == "zh" else "English"
    return (
        "You are a crypto due-diligence analyst reviewing a Robinhood-chain memecoin. "
        "If the files ~/.claude/skills/clawby-data/report/report-guide.md and report.css exist, "
        "READ them and follow that professional report structure and styling. "
        "The pre-gathered on-chain + X data for this token is in ./wiki/ "
        "(read ALL files there). Using that data, write a single self-contained, print-friendly "
        "styled HTML report to ./report.html — A4 width, apply the clawby report.css look (light "
        "theme, clean typography, header with token name/symbol/CA). Sections: 1) Snapshot (mcap, "
        "price, 24h volume, holders); 2) On-chain analysis (creator reputation, transfer activity, "
        "liquidity & ETH in pools); 3) Holder distribution & smart-money/KOL presence; 4) Social/X "
        "sentiment (quote notable posts); 5) Risk assessment (concentration, liquidity, dev history, "
        "red flags); 6) Verdict — bullish/neutral/bearish with concise reasoning. Ground every claim "
        "in the wiki data. WRITE THE ENTIRE REPORT IN " + langname.upper() +
        ". End with a 'not investment advice' disclaimer."
    )


def set_report_dir(path):
    global REPORT_DIR
    REPORT_DIR = os.path.abspath(os.path.expanduser(path))


def get_report_dir():
    return REPORT_DIR


_now = util.now_iso
_today = util.today
_retry = util.retry


def _subenv():
    """Env for the claude / Chrome subprocesses — restore the proxy that run.sh
    saved (the app's own httpx runs proxy-free, but claude needs it to reach the API)."""
    env = dict(os.environ)
    # this session runs at max effort; don't let the analysis subprocess inherit that (slow)
    for k in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_EFFORT",
              "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID"):
        env.pop(k, None)
    for lc, uc, saved in (("http_proxy", "HTTP_PROXY", "SAVED_HTTP_PROXY"),
                          ("https_proxy", "HTTPS_PROXY", "SAVED_HTTPS_PROXY"),
                          ("all_proxy", "ALL_PROXY", "SAVED_ALL_PROXY")):
        v = os.environ.get(saved)
        if v:
            env[lc] = v
            env[uc] = v
    return env


# ---------------- gather ----------------
async def _x_tweets(ca):
    d = await clawby.relay("x_search", {"query": ca, "count": 100, "start_date": _today()})
    items = d if isinstance(d, list) else (
        (d.get("tweets") or d.get("results") or d.get("items") or []) if isinstance(d, dict) else [])
    out = []
    for it in (items or [])[:40]:
        if not isinstance(it, dict):
            continue
        u = it.get("user") or {}
        out.append({
            "author": u.get("screen_name") or u.get("username") or u.get("name"),
            "text": (it.get("text") or "")[:400],
            "likes": it.get("like_count"), "retweets": it.get("retweet_count"),
            "views": it.get("view_count"), "created_at": it.get("created_at"),
        })
    return out


async def _gather(ca):
    tok = await _retry(lambda: sources.rh_token(ca), ok=lambda r: bool(r and r.get("symbol")))
    tok = tok or {}
    creator = tok.get("creator")                     # GMGN dev.creator_address (the real launcher)
    platform = tok.get("platform")
    creator_prof = await wallets.classify(creator) if creator else {}

    holders = await sources.rh_holders(ca, 40)
    holder_profs = []
    if holders:
        profs = await asyncio.gather(*[wallets.classify(h) for h in holders])
        for h, p in zip(holders, profs):
            holder_profs.append({"address": h, "is_kol": p.get("is_kol"),
                                 "is_smart": p.get("is_smart"), "twitter": p.get("twitter"),
                                 "tags": p.get("tags")})

    latest = await _retry(lambda: clawby.block_number(), ok=lambda r: bool(r))
    frm = max(0, (latest or 0) - 20000)
    logs = await clawby.get_logs(ca, TRANSFER_TOPIC, frm, latest) if latest else []
    senders, receivers = set(), set()
    for lg in logs:
        t = lg.get("topics") or []
        if len(t) >= 3:
            senders.add("0x" + t[1][-40:])
            receivers.add("0x" + t[2][-40:])

    pools = await clawby.relay("dexscreener_token_pools", {"chainId": "robinhood", "tokenAddresses": ca})
    pairs = pools.get("pairs") if isinstance(pools, dict) else pools
    pairs = pairs if isinstance(pairs, list) else []

    return {
        "ca": ca, "token": tok, "creator": creator, "deployer": platform,
        "creator_profile": creator_prof, "creation_tx": None,
        "created_ts": tok.get("created_ts"),
        "holders": holder_profs,
        "kol_count": sum(1 for h in holder_profs if h.get("is_kol")),
        "smart_count": sum(1 for h in holder_profs if h.get("is_smart")),
        "transfers": {"window_blocks": (latest - frm) if latest else 0, "count": len(logs),
                      "unique_senders": len(senders), "unique_receivers": len(receivers)},
        "pools": [{"dex": p.get("dexId"), "liq_usd": (p.get("liquidity") or {}).get("usd"),
                   "vol24": (p.get("volume") or {}).get("h24"), "price": p.get("priceUsd"),
                   "eth": (p.get("liquidity") or {}).get("quote"),
                   "quote": (p.get("quoteToken") or {}).get("symbol"),
                   "pair": p.get("pairAddress")} for p in pairs],
        "x_tweets": await _x_tweets(ca), "gathered_at": _now(),
    }


# ---------------- wiki ----------------
def _write_wiki(wiki_dir, d):
    os.makedirs(wiki_dir, exist_ok=True)
    tok = d.get("token") or {}
    cp = d.get("creator_profile") or {}

    ov = ["# %s (%s)\n" % (tok.get("name") or "?", tok.get("symbol") or "?"),
          "- Chain: Robinhood (EVM, chain id 4663)",
          "- CA: `%s`" % d["ca"],
          "- Market cap: %s" % tok.get("mcap"),
          "- Price: %s" % tok.get("price"),
          "- 24h volume: %s" % tok.get("volume24"),
          "- Holders: %s" % tok.get("holders"),
          "- Logo: %s" % tok.get("icon"),
          "- Gathered: %s" % d["gathered_at"]]
    _w(wiki_dir, "00-overview.md", "\n".join(ov))

    oc = ["# On-chain",
          "## Creator (real launcher wallet)",
          "- Launcher address: `%s`" % d.get("creator"),
          "- Launchpad platform: %s" % d.get("deployer"),
          "- Created: %s" % (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(d["created_ts"])) if d.get("created_ts") else "—"),
          "- Creator is KOL: %s (twitter @%s)" % (cp.get("is_kol"), cp.get("twitter")),
          "- Creator is smart-money: %s" % cp.get("is_smart"),
          "- Creator tags: %s" % (cp.get("tags")),
          "## Transfer activity (recent ~%s blocks)" % d["transfers"]["window_blocks"],
          "- Transfer events: %s" % d["transfers"]["count"],
          "- Unique senders: %s" % d["transfers"]["unique_senders"],
          "- Unique receivers: %s" % d["transfers"]["unique_receivers"],
          "## Liquidity pools"]
    for p in d["pools"]:
        oc.append("- %s: liq $%s, 24h vol $%s, %s %s in pool, price %s, pair `%s`" % (
            p["dex"], p["liq_usd"], p["vol24"], p.get("eth"), p.get("quote"), p["price"], p["pair"]))
    if not d["pools"]:
        oc.append("- (no DEX pool — likely still on the bonding curve / not graduated)")
    _w(wiki_dir, "01-onchain.md", "\n".join(oc))

    hl = ["# Holders (sampled top %d)" % len(d["holders"]),
          "- KOL holders: %s" % d["kol_count"],
          "- Smart-money holders: %s" % d["smart_count"], "", "| address | KOL | smart | twitter | tags |", "|---|---|---|---|---|"]
    for h in d["holders"]:
        hl.append("| `%s` | %s | %s | %s | %s |" % (h["address"], h["is_kol"], h["is_smart"],
                                                    ("@" + h["twitter"]) if h.get("twitter") else "", ",".join(h.get("tags") or [])))
    _w(wiki_dir, "02-holders.md", "\n".join(hl))

    xs = ["# X / Twitter (query = CA, today)", "- Posts found: %s" % len(d["x_tweets"]), ""]
    for tw in d["x_tweets"]:
        xs.append("### @%s  (♥%s ↻%s 👁%s)  %s" % (tw.get("author"), tw.get("likes"),
                  tw.get("retweets"), tw.get("views"), tw.get("created_at")))
        xs.append("> " + (tw.get("text") or "").replace("\n", " "))
        xs.append("")
    if not d["x_tweets"]:
        xs.append("(no X posts mention this CA in the window)")
    _w(wiki_dir, "03-social-x.md", "\n".join(xs))


def _w(d, name, text):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(text + "\n")


# ---------------- subprocess steps (pluggable analysis engine) ----------------
async def _spawn(argv, work_dir):
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=work_dir, stdin=asyncio.subprocess.DEVNULL, env=_subenv(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=ENGINE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("%s analysis timed out" % ENGINE)
    return (out or b"").decode("utf-8", "replace")


async def _run_claude(work_dir):
    claude = shutil.which("claude") or "claude"
    return await _spawn(
        [claude, "-p", _prompt(), "--permission-mode", "acceptEdits", "--model", "sonnet"], work_dir)


async def _run_codex(work_dir):
    codex = shutil.which("codex") or "codex"
    # non-interactive; workspace-write lets it create report.html in work_dir
    return await _spawn(
        [codex, "exec", "--cd", work_dir, "--sandbox", "workspace-write",
         "--skip-git-repo-check", _prompt()], work_dir)


async def _run_engine(work_dir):
    return await (_run_codex(work_dir) if ENGINE == "codex" else _run_claude(work_dir))


async def _to_pdf(html_path, pdf_path):
    proc = await asyncio.create_subprocess_exec(
        CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        "--print-to-pdf=%s" % pdf_path, html_path,
        stdin=asyncio.subprocess.DEVNULL, env=_subenv(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    await asyncio.wait_for(proc.communicate(), timeout=90)
    return os.path.exists(pdf_path)


# ---------------- orchestration ----------------
async def run_analysis(ca):
    ca = ca.lower()
    JOBS[ca] = {"status": "gathering", "step": "抓取链上 + X 数据", "started": _now(),
                "finished": None, "report": None, "error": None}
    try:
        data = await _gather(ca)
        work = os.path.join(ANALYSIS_DIR, ca)
        _write_wiki(os.path.join(work, "wiki"), data)
        with open(os.path.join(work, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)

        JOBS[ca].update(status="analyzing", step="本地 %s 分析中" % ENGINE, engine=ENGINE)
        html = os.path.join(work, "report.html")
        log = ""
        for attempt in range(2):          # the CLI can blip a transient timeout; retry once
            log = await _run_engine(work)
            if os.path.exists(html):
                break
            JOBS[ca].update(step="%s 重试中" % ENGINE)
        if not os.path.exists(html):
            raise RuntimeError("%s did not write report.html — tail:\n%s" % (ENGINE, log[-600:]))

        JOBS[ca].update(status="rendering", step="Chrome 渲染 PDF")
        os.makedirs(REPORT_DIR, exist_ok=True)
        sym = ((data.get("token") or {}).get("symbol") or "token").replace("/", "_")
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        pdf = os.path.join(REPORT_DIR, "%s-%s-%s.pdf" % (sym, ca[:8], ts))
        if not await _to_pdf(html, pdf):
            raise RuntimeError("Chrome failed to render the PDF")

        JOBS[ca].update(status="done", step="完成", report=pdf, finished=_now())
        _cleanup()
    except Exception as e:  # noqa: BLE001
        JOBS[ca].update(status="error", step="失败", error=str(e), finished=_now())


def _cleanup(keep=25):
    """Keep only the newest `keep` report PDFs and analysis dirs."""
    for base, isdir in ((REPORT_DIR, False), (ANALYSIS_DIR, True)):
        try:
            items = [os.path.join(base, x) for x in os.listdir(base)]
            items = [p for p in items if (os.path.isdir(p) if isdir else p.endswith(".pdf"))]
            items.sort(key=os.path.getmtime)
            for p in items[:-keep]:
                shutil.rmtree(p, ignore_errors=True) if isdir else os.remove(p)
        except Exception:  # noqa: BLE001
            pass

"""Async Clawby client: /api/relay (data) + /api/rpc (EVM chains).

Global ~5 req/s limiter, retry/backoff on 429/405/timeout, chunked eth_getLogs.
Extended for the multi-window monitor: per-chain rpc, eth_getTransactionCount,
and a wallet KOL/smart-money tag lookup (dex_wallet_stats.common).
"""
import asyncio
import os
import re
import time

import httpx

import util

BASE = os.environ.get("CLAWBY_BASE", "https://api.openclawby.com")
KEY = os.environ.get("CLAWBY_API_KEY", "")
DEFAULT_CHAIN = "robinhood"

MAX_LOG_RANGE = 3000
_calls = 0
_ban_until = 0.0        # unix ts until which upstream has rate-limit-banned us (back off)
_ban_streak = 0         # consecutive bans → escalate the cooldown until it clears


def banned_for():
    return max(0, int(_ban_until - time.time()))

# Concurrency: how many requests may be in flight at once (adjustable from the UI).
CONCURRENCY = 10
_sem = asyncio.Semaphore(CONCURRENCY)

RATE_PER_SEC = 6.0    # plan cap is 360/min; space Clawby calls to stay under it (avoid 429 churn)
_limiter = util.RateLimiter(RATE_PER_SEC)


def set_concurrency(n):
    global CONCURRENCY, _sem
    CONCURRENCY = max(1, min(100, int(n)))
    _sem = asyncio.Semaphore(CONCURRENCY)


def get_concurrency():
    return CONCURRENCY


def slot():
    """Current concurrency semaphore (shared by clawby + sources)."""
    return _sem


_client = None


def _http():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0, headers={"X-API-Key": KEY},
                                    follow_redirects=True)
    return _client


def call_count():
    return _calls


def _note_ban(text):
    """An upstream rate-limit ban (429 RATE_LIMIT_BANNED). The ban is ROLLING —
    reset_at keeps sliding forward while we keep hitting it — so enforce a real
    fixed cooldown (no Clawby calls) to let the window actually clear."""
    global _ban_until, _ban_streak
    _ban_streak += 1
    cool = time.time() + min(90 * _ban_streak, 600)      # escalate 90s→180s→…→10min until it clears
    m = re.search(r'reset_at"?\s*:?\s*"?(\d{10})', text or "")
    if m:
        cool = max(cool, float(m.group(1)) + 10)
    _ban_until = max(_ban_until, cool)


async def _post(path, body, retries=4):
    global _calls, _ban_streak
    last_err = None
    for attempt in range(retries):
        if _ban_until - time.time() > 0:                 # in a cooldown → do NOT hit upstream at all
            return {"__error__": "rate_banned"}          # (hammering while banned only extends it)
        try:
            await _limiter.acquire()
            async with _sem:
                _calls += 1
                r = await _http().post(BASE + path, json=body)
            txt = r.text
            # An upstream rate-limit ban arrives with the marker in the BODY regardless
            # of the HTTP status (seen as 200, 429, AND 502) — detect it by body, not code.
            if "RATE_LIMIT_BANNED" in txt or "temporarily banned" in txt:
                _note_ban(txt)                           # cool down + stop (hammering only extends it)
                return {"__error__": "rate_banned"}
            if r.status_code == 200:
                _ban_streak = 0                          # a clean success clears the escalation
                return r.json()
            if r.status_code in (429, 405, 500, 502, 503, 504):
                last_err = "HTTP %s" % r.status_code
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            return {"__error__": "HTTP %s" % r.status_code}
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = repr(e)
            await asyncio.sleep(0.4 * (2 ** attempt))
    return {"__error__": last_err or "max_retries"}


async def relay(name, params):
    d = await _post("/api/relay", {"name": name, "params": params})
    if isinstance(d, dict):
        if "__error__" in d:
            return None
        return d.get("data", d)
    return None


async def rpc(method, params, chain=DEFAULT_CHAIN):
    d = await _post("/api/rpc", {"chain": chain, "method": method, "params": params})
    if isinstance(d, dict):
        data = d.get("data", d)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
    return None


async def block_number(chain=DEFAULT_CHAIN):
    r = await rpc("eth_blockNumber", [], chain)
    return int(r, 16) if isinstance(r, str) else None


async def get_logs(address, topic0, from_block, to_block, chain=DEFAULT_CHAIN):
    out = []
    lo = from_block
    while lo <= to_block:
        hi = min(lo + MAX_LOG_RANGE - 1, to_block)
        res = await rpc("eth_getLogs", [{
            "address": address, "topics": [topic0],
            "fromBlock": hex(lo), "toBlock": hex(hi),
        }], chain)
        if isinstance(res, list):
            out.extend(res)
        lo = hi + 1
    return out


async def tx_count(address, chain):
    """Number of transactions sent from an address on `chain` (the nonce)."""
    r = await rpc("eth_getTransactionCount", [address, "latest"], chain)
    return int(r, 16) if isinstance(r, str) else None


async def wallet_tags(address, chain="bsc"):
    """A wallet's global GMGN profile: KOL / smart-money tags + twitter identity.

    `common` is chain-independent identity, so one lookup (bsc) is enough.
    """
    blank = {"tags": [], "twitter": None, "twitter_name": None, "fans": None,
             "is_kol": False, "is_smart": False}
    d = util.dig(await relay("dex_wallet_stats", {"chain": chain, "wallet_address": address, "period": "7d"}))
    common = d.get("common") if isinstance(d, dict) else None
    if not isinstance(common, dict):
        return blank
    tags = [t.lower() for t in (common.get("tags") or []) if isinstance(t, str)]
    return {
        "tags": tags,
        "twitter": common.get("twitter_username"),
        "twitter_name": common.get("twitter_name"),
        "fans": common.get("twitter_fans_num"),
        "is_kol": "kol" in tags,
        "is_smart": any(t in tags for t in ("smart_money", "app_smart_money", "smart_degen")),
    }

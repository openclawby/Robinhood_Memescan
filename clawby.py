"""Async Clawby client: /api/relay (data) + /api/rpc (EVM chains).

Global ~5 req/s limiter, retry/backoff on 429/405/timeout, chunked eth_getLogs.
Extended for the multi-window monitor: per-chain rpc, eth_getTransactionCount,
and a wallet KOL/smart-money tag lookup (dex_wallet_stats.common).
"""
import asyncio
import os

import httpx

import util

BASE = os.environ.get("CLAWBY_BASE", "https://api.openclawby.com")
KEY = os.environ.get("CLAWBY_API_KEY", "")
DEFAULT_CHAIN = "robinhood"

MAX_LOG_RANGE = 3000
_calls = 0

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


async def _post(path, body, retries=4):
    global _calls
    last_err = None
    for attempt in range(retries):
        try:
            await _limiter.acquire()
            async with _sem:
                _calls += 1
                r = await _http().post(BASE + path, json=body)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 405, 500, 502, 503, 504):
                last_err = "HTTP %s" % r.status_code
                await asyncio.sleep(0.4 * (2 ** attempt))
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

"""Robinhood token data via Clawby's relay (GMGN + DexScreener providers).

Everything here goes through Clawby — NO Blockscout. Clawby fully covers the
Robinhood chain:

  - dex_trending      → tokens ranked by 24h volume (the W2 / W4 universe), each
                        row already carrying volume / holders / swaps / liquidity /
                        market cap / creation timestamp / launchpad / creator.
  - dex_token_info    → full per-CA snapshot (used by W3 + deep analysis).
  - dex_token_holders → top-holder list (for KOL / smart-money sampling).
  - dexscreener_*      → pool ETH reserves (via clawby relay too).

The one non-Clawby call left is HyperEVM's tx-count (Clawby has no hyperevm chain).
"""
import json

import httpx

import clawby
import util

HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"

# stablecoins / wrapped / infra tokens to drop from the meme ranking (defensive;
# dex_trending is already a memecoin feed, but occasionally surfaces these)
NON_MEME = {
    "USDE", "USDG", "USDC", "USDT", "DAI", "USDD", "FDUSD", "SUSDE", "SUSDS",
    "WETH", "ETH", "WBTC", "CBBTC", "TBTC", "WHOOD", "HOOD",
    "SOL", "BNB", "STETH", "WSTETH", "WEETH", "SYRUPUSDG",
}

_f = util.f
_i = util.i
_token_cache = util.TTLCache(120)     # per-CA dex_token_info snapshot

_client = None


def _http():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "rh-monitor-demo"},
                                    follow_redirects=True)
    return _client


def _unwrap(x):
    """Peel Clawby/GMGN's nested {code,data} envelopes (data is sometimes a JSON
    string) and return the innermost payload dict (e.g. {rank:[…]} / {list:[…]} /
    the token object)."""
    for _ in range(6):
        if isinstance(x, str):
            try:
                x = json.loads(x)
            except (ValueError, TypeError):
                return None
        if isinstance(x, dict) and "data" in x and set(x) <= {"code", "data", "message", "reason"}:
            x = x["data"]
            continue
        return x
    return x


def _is_meme(sym):
    if not sym:
        return True
    s = sym.upper()
    return s not in NON_MEME and "USD" not in s


def _norm_row(it):
    """A dex_trending rank item → our token schema (all fields already present)."""
    return {
        "ca": (it.get("address") or "").lower(),
        "symbol": it.get("symbol"), "name": it.get("name"), "icon": it.get("logo"),
        "price": _f(it.get("price")),
        "mcap": _f(it.get("market_cap")),
        "volume24": _f(it.get("volume")),
        "liquidity": _f(it.get("liquidity")),
        "holders": _i(it.get("holder_count")),
        "transfers": _i(it.get("swaps")),                 # 24h swap (trade) count
        # creation_timestamp = real contract deploy (matches on-chain); open_timestamp
        # is the bonding-curve graduation/migration time — NOT when the coin was created.
        "created_ts": _i(it.get("creation_timestamp") or it.get("open_timestamp")),
        "platform": it.get("launchpad_platform") or it.get("launchpad"),
        "creator": (it.get("creator") or "").lower() or None,
        "smart": _i(it.get("smart_degen_count")),
        "twitter": it.get("twitter_username"),
    }


async def rh_trending(interval="24h", order_by="volume", limit=50, extra=None):
    params = {"chain": "robinhood", "interval": interval, "order_by": order_by,
              "limit": limit, "direction": "desc"}
    if extra:
        params.update(extra)
    payload = _unwrap(await clawby.relay("dex_trending", params))
    rank = payload.get("rank") if isinstance(payload, dict) else None
    if not isinstance(rank, list):
        return []
    return [_norm_row(it) for it in rank if it.get("address") and _is_meme(it.get("symbol"))]


async def rh_top_tokens(limit=100, pages=2):
    """Top tokens by 24h volume. A single dex_trending response is capped near ~70
    rich rows, so page down by volume with max_volume and merge."""
    seen, floor = {}, None
    for _ in range(max(1, pages)):
        batch = await rh_trending(limit=50, extra=({"max_volume": floor} if floor is not None else None))
        if not batch:
            break
        for t in batch:
            if t["ca"]:
                seen.setdefault(t["ca"], t)
        low = min((t["volume24"] or 0) for t in batch)
        if floor is not None and low >= floor:        # no downward progress → stop paging
            break
        floor = low
        if len(seen) >= limit + 10:
            break
    rows = sorted(seen.values(), key=lambda t: (t["volume24"] or 0), reverse=True)
    return rows[:limit]


async def rh_token(ca):
    """Full per-CA snapshot from dex_token_info (cached, short TTL)."""
    hit = _token_cache.get(ca)
    if hit is not None:
        return hit
    info = _unwrap(await clawby.relay("dex_token_info", {"chain": "robinhood", "address": ca}))
    if not isinstance(info, dict) or not info.get("symbol"):
        return {}
    price = info.get("price") if isinstance(info.get("price"), dict) else {}
    dev = info.get("dev") if isinstance(info.get("dev"), dict) else {}
    px = _f(price.get("price"))
    circ = _f(info.get("circulating_supply"))
    out = {
        "symbol": info.get("symbol"), "name": info.get("name"), "icon": info.get("logo"),
        "holders": _i(info.get("holder_count")),
        "price": px,
        "volume24": _f(price.get("volume_24h")),
        "transfers": _i(price.get("swaps_24h")),
        "mcap": (px * circ) if (px and circ) else None,
        "liquidity": _f(info.get("liquidity")),
        "created_ts": _i(info.get("creation_timestamp") or info.get("open_timestamp")),  # real deploy, not migration
        "platform": info.get("launchpad_platform"),
        "creator": (dev.get("creator_address") or info.get("creator") or "").lower() or None,
        "trading": {
            "buys_24h": _i(price.get("buys_24h")), "sells_24h": _i(price.get("sells_24h")),
            "swaps_24h": _i(price.get("swaps_24h")),
            "vol_1h": _f(price.get("volume_1h")), "vol_6h": _f(price.get("volume_6h")),
            "vol_24h": _f(price.get("volume_24h")),
            "price_1h": _f(price.get("price_1h")), "price_6h": _f(price.get("price_6h")),
            "price_24h": _f(price.get("price_24h")),
        } if price else {},
    }
    _token_cache.set(ca, out)
    return out


async def rh_by_mcap(min_mcap, max_mcap, limit=120, pages=3):
    """Tokens whose market cap is in [min_mcap, max_mcap], via dex_trending ordered
    by marketcap (paged down with max_marketcap)."""
    seen, ceil = {}, max_mcap
    for _ in range(max(1, pages)):
        rows = await rh_trending(order_by="marketcap", limit=50,
                                 extra={"min_marketcap": min_mcap, "max_marketcap": ceil})
        rows = [t for t in rows if t.get("mcap") and min_mcap <= t["mcap"] <= max_mcap]
        if not rows:
            break
        for t in rows:
            if t["ca"]:
                seen.setdefault(t["ca"], t)
        low = min(t["mcap"] for t in rows)
        if low >= ceil:                    # no downward progress → stop
            break
        ceil = low
        if len(seen) >= limit + 10:
            break
    return sorted(seen.values(), key=lambda t: (t["mcap"] or 0), reverse=True)[:limit]


async def rh_holders(ca, limit=30):
    """Top holder wallet addresses (skips the liquidity-pool entries).

    An explicit `limit` is REQUIRED — omitting it makes the relay return a huge
    holder blob that overflows the 200k response cap and can't be parsed.
    """
    payload = _unwrap(await clawby.relay(
        "dex_token_holders", {"chain": "robinhood", "address": ca, "limit": max(20, min(100, limit))}))
    lst = payload.get("list") if isinstance(payload, dict) else None
    out = []
    for it in (lst if isinstance(lst, list) else []):
        if it.get("addr_type") == 2:                   # 2 = liquidity pool, not a real holder
            continue
        a = it.get("address") or it.get("account_address")
        if isinstance(a, str) and a.startswith("0x"):
            out.append(a.lower())
        if len(out) >= limit:
            break
    return out


async def hyperevm_tx_count(address):
    try:
        async with clawby.slot():
            r = await _http().post(HYPEREVM_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_getTransactionCount", "params": [address, "latest"],
            })
        v = r.json().get("result")
        return int(v, 16) if isinstance(v, str) else None
    except Exception:
        return None

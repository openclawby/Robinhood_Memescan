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
_token_cache = util.TTLCache(120)     # (chain,ca) dex_token_info snapshot

CHAINS = ("robinhood", "bsc", "base")   # chains the AI scoring / custom report support

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


async def rh_trending(interval="24h", order_by="volume", limit=50, extra=None, chain="robinhood"):
    params = {"chain": chain, "interval": interval, "order_by": order_by,
              "limit": limit, "direction": "desc"}
    if extra:
        params.update(extra)
    payload = _unwrap(await clawby.relay("dex_trending", params))
    rank = payload.get("rank") if isinstance(payload, dict) else None
    if not isinstance(rank, list):
        return []
    return [_norm_row(it) for it in rank if it.get("address") and _is_meme(it.get("symbol"))]


async def rh_top_tokens(limit=100, pages=2, chain="robinhood"):
    """Top tokens by 24h volume. A single dex_trending response is capped near ~70
    rich rows, so page down by volume with max_volume and merge."""
    seen, floor = {}, None
    for _ in range(max(1, pages)):
        batch = await rh_trending(limit=50, chain=chain,
                                  extra=({"max_volume": floor} if floor is not None else None))
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


async def rh_token(ca, chain="robinhood"):
    """Full per-CA snapshot from dex_token_info (cached, short TTL). Includes the
    `stat` holder-relationship signals (top-10 concentration / sniper / bundler /
    fresh-wallet / dev-hold) used by the custom report."""
    key = "%s:%s" % (chain, ca)
    hit = _token_cache.get(key)
    if hit is not None:
        return hit
    info = _unwrap(await clawby.relay("dex_token_info", {"chain": chain, "address": ca}))
    if not isinstance(info, dict) or not info.get("symbol"):
        return {}
    price = info.get("price") if isinstance(info.get("price"), dict) else {}
    dev = info.get("dev") if isinstance(info.get("dev"), dict) else {}
    stat = info.get("stat") if isinstance(info.get("stat"), dict) else {}
    px = _f(price.get("price"))
    circ = _f(info.get("circulating_supply"))
    out = {
        "chain": chain,
        "symbol": info.get("symbol"), "name": info.get("name"), "icon": info.get("logo"),
        "holders": _i(info.get("holder_count")),
        "price": px,
        "volume24": _f(price.get("volume_24h")),
        "transfers": _i(price.get("swaps_24h")),
        "mcap": (px * circ) if (px and circ) else None,
        "total_supply": _f(info.get("total_supply")),
        "liquidity": _f(info.get("liquidity")),
        "created_ts": _i(info.get("creation_timestamp") or info.get("open_timestamp")),  # real deploy, not migration
        "platform": info.get("launchpad_platform"),
        "creator": (dev.get("creator_address") or info.get("creator") or "").lower() or None,
        "twitter": info.get("twitter_username"), "website": info.get("website"), "telegram": info.get("telegram"),
        "trading": {
            "buys_24h": _i(price.get("buys_24h")), "sells_24h": _i(price.get("sells_24h")),
            "swaps_24h": _i(price.get("swaps_24h")),
            "swaps_1h": _i(price.get("swaps_1h")), "swaps_6h": _i(price.get("swaps_6h")),
            "vol_1h": _f(price.get("volume_1h")), "vol_6h": _f(price.get("volume_6h")),
            "vol_24h": _f(price.get("volume_24h")),
            "price_1h": _f(price.get("price_1h")), "price_6h": _f(price.get("price_6h")),
            "price_24h": _f(price.get("price_24h")),
        } if price else {},
        "stat": {
            "top_10_holder_rate": _f(stat.get("top_10_holder_rate")),
            "sniper_hold_rate": _f(stat.get("top70_sniper_hold_rate")),
            "bundler_rate": _f(stat.get("top_bundler_trader_percentage")),
            "rat_trader_rate": _f(stat.get("top_rat_trader_percentage")),
            "fresh_wallet_rate": _f(stat.get("fresh_wallet_rate")),
            "dev_hold_rate": _f(stat.get("dev_team_hold_rate") or stat.get("creator_hold_rate")),
            "bot_degen_rate": _f(stat.get("bot_degen_rate")),
            "signal_count": _i(stat.get("signal_count")),
            "smart_degen_count": _i(stat.get("degen_call_count")),
        },
        "security": {
            "is_honeypot": info.get("is_honeypot"), "buy_tax": _f(info.get("buy_tax")),
            "sell_tax": _f(info.get("sell_tax")), "renounced": info.get("renounced_mint"),
        },
    }
    _token_cache.set(key, out)
    return out


async def rh_by_mcap(min_mcap, max_mcap, limit=120, pages=3, chain="robinhood"):
    """Tokens whose market cap is in [min_mcap, max_mcap], via dex_trending ordered
    by marketcap (paged down with max_marketcap)."""
    seen, ceil = {}, max_mcap
    for _ in range(max(1, pages)):
        rows = await rh_trending(order_by="marketcap", limit=50, chain=chain,
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


def _holder_records(payload, limit):
    lst = payload.get("list") if isinstance(payload, dict) else None
    out = []
    for it in (lst if isinstance(lst, list) else []):
        a = it.get("address") or it.get("account_address")
        if not (isinstance(a, str) and a.startswith("0x")):
            continue
        out.append({"address": a.lower(), "is_pool": it.get("addr_type") == 2,
                    "balance": _f(it.get("balance") or it.get("amount_cur")),
                    "usd": _f(it.get("usd_value")),
                    "pct": _f(it.get("amount_percentage")),
                    "exchange": it.get("exchange")})
        if len(out) >= limit:
            break
    return out


async def rh_holders(ca, limit=30, chain="robinhood"):
    """Top holder wallet addresses (skips the liquidity-pool entries). An explicit
    `limit` is REQUIRED — omitting it overflows the 200k response cap."""
    payload = _unwrap(await clawby.relay(
        "dex_token_holders", {"chain": chain, "address": ca, "limit": max(20, min(100, limit))}))
    return [r["address"] for r in _holder_records(payload, limit + 5) if not r["is_pool"]][:limit]


async def rh_holder_records(ca, limit=50, chain="robinhood"):
    """Full top-holder records (address / balance / usd / share / pool-flag) for the report."""
    payload = _unwrap(await clawby.relay(
        "dex_token_holders", {"chain": chain, "address": ca, "limit": max(20, min(100, limit))}))
    return _holder_records(payload, limit)


async def rh_traders(ca, limit=25, chain="robinhood"):
    """Top traders by realized PnL (dex_token_traders) — for the report's trading table."""
    payload = _unwrap(await clawby.relay(
        "dex_token_traders", {"chain": chain, "address": ca, "limit": max(10, min(100, limit))}))
    lst = payload.get("list") if isinstance(payload, dict) else (payload if isinstance(payload, list) else None)
    out = []
    for it in (lst if isinstance(lst, list) else []):
        a = it.get("address") or it.get("wallet_address")
        out.append({"address": (a or "").lower(),
                    "realized_profit": _f(it.get("realized_profit") or it.get("profit")),
                    "buys": _i(it.get("buy_tx_count") or it.get("buys")),
                    "sells": _i(it.get("sell_tx_count") or it.get("sells")),
                    "tags": [t for t in (it.get("tags") or []) if isinstance(t, str)]})
    return out[:limit]


async def rh_kline(ca, chain="robinhood", resolution="1h"):
    """OHLC candles (time/open/high/low/close/volume) for price + volume charts."""
    payload = _unwrap(await clawby.relay(
        "dex_token_kline", {"chain": chain, "address": ca, "resolution": resolution}))
    lst = payload.get("list") if isinstance(payload, dict) else (payload if isinstance(payload, list) else None)
    out = []
    for it in (lst if isinstance(lst, list) else []):
        out.append({"t": _i(it.get("time")), "o": _f(it.get("open")), "h": _f(it.get("high")),
                    "l": _f(it.get("low")), "c": _f(it.get("close")), "v": _f(it.get("volume"))})
    return [k for k in out if k["t"]]


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

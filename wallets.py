"""KOL / smart-money classification for wallets, with a per-address cache.

Robinhood is EVM, so a wallet's GMGN tags (from bsc lookup) apply here too.
"""
import asyncio

import clawby
import util

_cache = util.Capped(20000)   # address(lower) -> profile dict (size-capped)


async def classify(address):
    a = (address or "").lower()
    if not a:
        return {"is_kol": False, "is_smart": False, "tags": [], "twitter": None}
    if a in _cache:
        return _cache[a]
    prof = await clawby.wallet_tags(a)
    _cache[a] = prof
    return prof


async def count_tags(addresses):
    """Classify a list of addresses; return KOL / smart-money counts + KOL handles."""
    uniq = list({(x or "").lower() for x in addresses if x})
    profs = await asyncio.gather(*[classify(a) for a in uniq]) if uniq else []
    kol = sum(1 for p in profs if p.get("is_kol"))
    smart = sum(1 for p in profs if p.get("is_smart"))
    handles = [p.get("twitter") for p in profs if p.get("is_kol") and p.get("twitter")]
    return {"kol": kol, "smart": smart, "kol_handles": handles[:10]}


def cache_size():
    return len(_cache)

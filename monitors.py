"""Three window monitors + shared state + controls.

W1  new-launch feed        : discover via factories, enrich creator (KOL + tx counts x5 chains)
W2  top-100 memes / 24h vol : Blockscout ranking + DexScreener liquidity + X search (incremental by CA)
W3  watchlist              : per-CA at user-set frequency, incl. KOL / smart-money (sampled holders)

Scans run concurrently (bounded by clawby's shared concurrency semaphore).
Each window has an independent enabled flag and an adjustable scan interval.
"""
import asyncio
import time
from collections import defaultdict

import clawby
import sources
import util
import wallets
from factories import PLATFORMS, decode_token

# ---- tunables ----
W1_INTERVAL = 5
W2_INTERVAL = 300
W3_TICK = 5
W4_INTERVAL = 300
SEED_BLOCKS = 6000
SEED_PER_PLATFORM = 10
W1_CAP = 60
W1_CREATOR_PER_TICK = 20
W2_LIMIT = 100
W3_HOLDER_SAMPLE = 30
W4_POOL = 200          # volume-ranked candidates to inspect for the "created <=3d" window
W4_LIMIT = 100         # top-N (by volume) among the recent ones to display
W4_MAX_AGE = 3 * 86400  # 3 days
TX_CHAINS = ["robinhood", "eth", "bsc", "base"]   # via Clawby RPC; hyperevm separate
SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"

# ---- window state (running / phase drive the UI activity feedback) ----
W1 = {"enabled": True, "interval": W1_INTERVAL, "tokens": {}, "last": None, "running": False, "phase": ""}
W2 = {"enabled": True, "interval": W2_INTERVAL, "tokens": {}, "rank": [], "last": None,
      "running": False, "phase": "", "done": 0, "total": 0}
W3 = {"enabled": True, "watch": {}, "last": None, "running": False, "phase": ""}
W4 = {"enabled": True, "interval": W4_INTERVAL, "tokens": {}, "rank": [], "last": None,
      "running": False, "phase": "", "done": 0, "total": 0}

LAST_BLOCK = {}
TS_CACHE = util.Capped(10000)      # block -> unix ts (size-capped)
CREATOR_CACHE = util.Capped(5000)  # creator -> {is_kol, twitter, tx} (size-capped)
CREATED_CACHE = util.Capped(8000)  # ca -> real creation unix ts (immutable; from dex_token_info)
STATUS = {"latest_block": None, "error": None}

now = util.now_iso
_f = util.f
_get = util.get


def decode_string(hexstr):
    if not hexstr or hexstr == "0x":
        return ""
    try:
        b = bytes.fromhex(hexstr[2:])
    except ValueError:
        return ""
    if len(b) >= 64:
        n = int.from_bytes(b[32:64], "big")
        if 0 < n <= len(b) - 64:
            return b[64:64 + n].decode("utf-8", "replace").rstrip("\x00")
    return b.rstrip(b"\x00").decode("utf-8", "replace")


async def eth_call(ca, data):
    r = await clawby.rpc("eth_call", [{"to": ca, "data": data}, "latest"])
    return r if isinstance(r, str) else "0x"


async def block_ts(bn):
    if bn in TS_CACHE:
        return TS_CACHE[bn]
    blk = await clawby.rpc("eth_getBlockByNumber", [hex(bn), False])
    if isinstance(blk, dict) and blk.get("timestamp"):
        ts = int(blk["timestamp"], 16)
        TS_CACHE[bn] = ts
        return ts
    return None


async def tx_from(txh):
    if not txh:
        return None
    tx = await clawby.rpc("eth_getTransactionByHash", [txh])
    return tx.get("from") if isinstance(tx, dict) else None


# ---- X search: last ~24h, single page (cap 100). 100 means "100 or more" (UI shows ≥100). ----
_x_items = util.x_items
_today = util.today
_xcount_cache = util.TTLCache(300)   # per-CA X mention count, 5-min TTL (shared W2/W3)


async def _x_count(query):
    hit = _xcount_cache.get(query)
    if hit is not None:
        return hit
    # day-granular date, so "current 24h" = today (UTC); one page, cap 100 (UI shows ≥100).
    # NOTE: sort=Latest + start_date makes the API cap at ~20, so we omit sort.
    items = _x_items(await clawby.relay("x_search", {"query": query, "count": 100, "start_date": _today()}))
    n = len(items)
    _xcount_cache.set(query, n)
    return n


async def _dex_liq(ca):
    d = await clawby.relay("dexscreener_token_pools", {"chainId": "robinhood", "tokenAddresses": ca})
    pairs = d.get("pairs") if isinstance(d, dict) else d
    pairs = pairs if isinstance(pairs, list) else []
    liq = sum((_f(_get(x, "liquidity", "usd")) or 0) for x in pairs)
    eth, got = 0.0, False
    for x in pairs:
        q = ((x.get("quoteToken") or {}).get("symbol") or "").upper()
        v = _f(_get(x, "liquidity", "quote"))
        if q in ("WETH", "ETH") and v is not None:
            eth += v
            got = True
    return (liq or None), (round(eth, 4) if got else None), len(pairs)


# ============================ W1 : new launches ============================
async def _scan_platform(p, latest):
    if not p.get("enabled"):
        return []
    fac = p["factory"].lower()
    seeding = fac not in LAST_BLOCK
    frm = max(0, latest - SEED_BLOCKS) if seeding else LAST_BLOCK[fac] + 1
    if frm > latest:
        LAST_BLOCK[fac] = latest
        return []
    logs = await clawby.get_logs(fac, p["topic0"], frm, latest)
    LAST_BLOCK[fac] = latest
    logs.sort(key=lambda l: int(l.get("blockNumber", "0x0"), 16))
    if seeding and len(logs) > SEED_PER_PLATFORM:
        logs = logs[-SEED_PER_PLATFORM:]
    out = []
    for lg in logs:
        ca = decode_token(p["decode"], lg)
        if ca and ca.lower() not in W1["tokens"]:
            out.append((ca.lower(), p, lg))
    return out


async def w1_discover():
    latest = await clawby.block_number()
    if not latest:
        return
    STATUS["latest_block"] = latest
    batches = await asyncio.gather(*[_scan_platform(p, latest) for p in PLATFORMS])
    new = {}
    for b in batches:
        for ca, p, lg in b:
            new.setdefault(ca, (p, lg))
    await asyncio.gather(*[_w1_add(ca, p, lg) for ca, (p, lg) in new.items()])
    _w1_prune()
    W1["last"] = now()


async def _w1_add(ca, platform, log):
    bn = int(log.get("blockNumber", "0x0"), 16)
    txh = log.get("transactionHash")
    sym = decode_string(await eth_call(ca, SEL_SYMBOL))
    if not sym and platform["decode"] == "data1":
        alt = "0x" + (log.get("data", "0x")[2:][3 * 64:4 * 64])[-40:]
        if len(alt) == 42:
            s2 = decode_string(await eth_call(alt, SEL_SYMBOL))
            if s2:
                ca, sym = alt.lower(), s2
            if ca in W1["tokens"]:
                return
    name = decode_string(await eth_call(ca, SEL_NAME))
    W1["tokens"][ca] = {
        "ca": ca, "platform": platform["name"], "symbol": sym, "name": name,
        "created_block": bn, "created_ts": await block_ts(bn), "creator": await tx_from(txh),
        "creator_is_kol": None, "creator_twitter": None,
        "tx_robinhood": None, "tx_eth": None, "tx_bsc": None, "tx_base": None, "tx_hyperevm": None,
        "enriched": False,
    }


async def _w1_enrich_one(t):
    cr = t["creator"].lower()
    if cr not in CREATOR_CACHE:
        res = await asyncio.gather(
            wallets.classify(cr),
            *[clawby.tx_count(cr, ch) for ch in TX_CHAINS],
            sources.hyperevm_tx_count(cr),
        )
        prof, rest = res[0], res[1:]
        tx = {ch: rest[i] for i, ch in enumerate(TX_CHAINS)}
        tx["hyperevm"] = rest[-1]
        CREATOR_CACHE[cr] = {"is_kol": prof.get("is_kol"), "twitter": prof.get("twitter"), "tx": tx}
    c = CREATOR_CACHE[cr]
    t["creator_is_kol"] = c["is_kol"]
    t["creator_twitter"] = c["twitter"]
    for ch in TX_CHAINS:
        t["tx_" + ch] = c["tx"].get(ch)
    t["tx_hyperevm"] = c["tx"].get("hyperevm")
    t["enriched"] = True


async def w1_enrich_creators():
    pending = [t for t in W1["tokens"].values() if not t["enriched"] and t.get("creator")]
    pending.sort(key=lambda t: (t["created_ts"] or 0), reverse=True)
    await asyncio.gather(*[_w1_enrich_one(t) for t in pending[:W1_CREATOR_PER_TICK]])


def _w1_prune():
    byp = defaultdict(list)
    for t in W1["tokens"].values():
        byp[t["platform"]].append(t)
    for lst in byp.values():
        lst.sort(key=lambda t: (t["created_ts"] or 0, t["created_block"]), reverse=True)
        for t in lst[W1_CAP:]:
            W1["tokens"].pop(t["ca"], None)


# ============================ shared market enrichment (W2 + W4) ============================
async def _enrich_market(t):
    """price/mcap/vol/holders/transfers/liquidity/creation/platform already arrive
    on the dex_trending row — here we only add pool-ETH (dexscreener) + X mentions."""
    dl, xc = await asyncio.gather(_dex_liq(t["ca"]), _x_count(t["ca"]))
    liq, eth, pools = dl
    if t.get("liquidity") is None:
        t["liquidity"] = liq
    t["eth_in_pools"], t["pools"] = eth, pools
    t["x_ca"] = xc


async def _enrich_prog(win, t, label):
    """Enrich one token and tick the window's progress phase for UI feedback."""
    await _enrich_market(t)
    win["done"] += 1
    win["phase"] = "%s %d/%d" % (label, win["done"], win["total"])


# ============================ W2 : top memes (incremental by CA) ============================
async def w2_refresh():
    if W2["running"]:
        return
    W2["running"] = True
    W2["phase"] = "拉取交易量榜单"
    try:
        tops = []
        for _ in range(3):                       # relay occasionally blips empty
            tops = await sources.rh_top_tokens(W2_LIMIT)
            if tops:
                break
            await asyncio.sleep(1.0)
        if not tops:                             # keep existing data — don't wipe the window
            util.logerr("w2: dex_trending empty after retries")
            W2["phase"] = "榜单为空，稍后重试"
            return
        merged = {}
        for t in tops:                       # merge by CA: keep prior enrichment, refresh ranking fields
            m = dict(W2["tokens"].get(t["ca"], {}))
            m.update(t)
            merged[t["ca"]] = m
        W2["tokens"] = merged
        W2["rank"] = [t["ca"] for t in tops]
        W2["last"] = now()
        W2["total"], W2["done"] = len(W2["rank"]), 0
        W2["phase"] = "补全指标 0/%d" % W2["total"]
        await asyncio.gather(*[_enrich_prog(W2, W2["tokens"][ca], "补全指标") for ca in W2["rank"]])
        W2["last"] = now()
        W2["phase"] = "完成 · %d 个" % len(W2["rank"])
    finally:
        W2["running"] = False


# ============================ W4 : coins created within 3 days, top-100 by volume ============================
async def _creation_ts(ca):
    """Real contract-creation ts via dex_token_info.creation_timestamp (immutable,
    matches on-chain). The dex_trending row's timestamp is the pool/graduation time,
    NOT the launch — so we can't trust it for the age filter."""
    if ca in CREATED_CACHE:
        return CREATED_CACHE[ca]
    info = await sources.rh_token(ca)
    ts = info.get("created_ts")
    if ts:
        CREATED_CACHE[ca] = ts
    return ts


async def w4_refresh():
    if W4["running"]:
        return
    W4["running"] = True
    W4["phase"] = "拉取交易量榜单"
    try:
        pool = await sources.rh_top_tokens(W4_POOL, pages=4)
        if not pool:
            util.logerr("w4: dex_trending empty")
            W4["phase"] = "榜单为空，稍后重试"
            return
        cutoff = time.time() - W4_MAX_AGE
        # Pre-filter: the row's pool/graduation ts is an upper bound on recency —
        # if even that is >3d old, the real creation is older too, so skip the lookup.
        maybe = [t for t in pool if (not t.get("created_ts")) or t["created_ts"] >= cutoff]
        W4["total"], W4["done"] = len(maybe), 0
        W4["phase"] = "校准创建时间 0/%d" % len(maybe)

        async def _res(t):
            real = await _creation_ts(t["ca"])
            if real:
                t["created_ts"] = real
            W4["done"] += 1
            W4["phase"] = "校准创建时间 %d/%d" % (W4["done"], W4["total"])
        await asyncio.gather(*[_res(t) for t in maybe])

        recent = [t for t in maybe if t.get("created_ts") and t["created_ts"] >= cutoff]
        recent.sort(key=lambda t: (t["volume24"] or 0), reverse=True)
        top = recent[:W4_LIMIT]
        W4["phase"] = "筛出 %d 个3天内新币" % len(top)
        merged = {}
        for t in top:
            m = dict(W4["tokens"].get(t["ca"], {}))
            m.update(t)
            merged[t["ca"]] = m
        W4["tokens"] = merged
        W4["rank"] = [t["ca"] for t in top]
        W4["last"] = now()
        W4["total"], W4["done"] = len(top), 0
        W4["phase"] = "补全指标 0/%d" % max(1, len(top))
        await asyncio.gather(*[_enrich_prog(W4, W4["tokens"][ca], "补全指标") for ca in W4["rank"]])
        W4["last"] = now()
        W4["phase"] = "完成 · %d 个3天内新币" % len(top)
    finally:
        W4["running"] = False


# ============================ W3 : watchlist ============================
async def w3_refresh_due():
    t = time.monotonic()
    due = []
    for ca, w in list(W3["watch"].items()):
        if t >= w["next_due"]:
            w["next_due"] = t + w["interval"]
            due.append((ca, w))
    if not due:
        return
    W3["running"] = True
    W3["phase"] = "刷新收藏 %d 个" % len(due)
    try:
        await asyncio.gather(*[_w3_safe(ca, w) for ca, w in due])
        W3["last"] = now()
        W3["phase"] = "完成"
    finally:
        W3["running"] = False


async def _w3_safe(ca, w):
    try:
        await _w3_refresh_one(ca, w)
    except Exception as e:  # noqa: BLE001
        w["data"] = {**(w.get("data") or {}), "error": str(e)}


async def _w3_refresh_one(ca, w):
    info, (liq, eth, pools), holders, xc = await asyncio.gather(
        sources.rh_token(ca), _dex_liq(ca), sources.rh_holders(ca, W3_HOLDER_SAMPLE), _x_count(ca))
    if not w.get("symbol") and info.get("symbol"):
        w["symbol"] = info.get("symbol")
    if not w.get("icon") and info.get("icon"):
        w["icon"] = info.get("icon")
    counts = await wallets.count_tags(holders) if holders else {"kol": 0, "smart": 0, "kol_handles": []}
    w["data"] = {
        "price": info.get("price"), "mcap": info.get("mcap"), "volume24": info.get("volume24"),
        "transfers": info.get("transfers"), "platform": info.get("platform"),
        "liquidity": info.get("liquidity") if info.get("liquidity") is not None else liq,
        "eth_in_pools": eth, "kol": counts["kol"], "smart": counts["smart"],
        "kol_handles": counts["kol_handles"], "x_ca": xc,
        "holders": info.get("holders"), "sampled": len(holders), "updated": now(),
    }


# ============================ controls ============================
def set_enabled(win, val):
    w = {"w1": W1, "w2": W2, "w3": W3, "w4": W4}.get(win)
    if w is not None:
        w["enabled"] = bool(val)
        return True
    return False


def set_platform_enabled(name, val):
    for p in PLATFORMS:
        if p["name"] == name:
            p["enabled"] = bool(val)
            if not p["enabled"]:
                LAST_BLOCK.pop(p["factory"].lower(), None)   # re-seed if re-enabled later
            return True
    return False


def platforms_state():
    return [{"name": p["name"], "enabled": p.get("enabled", False)} for p in PLATFORMS]


def set_interval(win, seconds):
    floors = {"w1": 2, "w2": 15, "w4": 30}
    w = {"w1": W1, "w2": W2, "w4": W4}.get(win)
    if w is not None:
        w["interval"] = max(floors[win], int(seconds))
        return True
    return False


def _symbol_for(ca):
    for store in (W1["tokens"], W2["tokens"], W4["tokens"]):
        if ca in store:
            return store[ca].get("symbol")
    return None


def add_watch(ca, interval):
    ca = ca.lower()
    W3["watch"][ca] = {"interval": max(10, int(interval)), "next_due": 0.0,
                       "data": {}, "added": now(), "symbol": _symbol_for(ca)}


def remove_watch(ca):
    W3["watch"].pop(ca.lower(), None)


def set_watch_interval(ca, interval):
    w = W3["watch"].get(ca.lower())
    if w:
        w["interval"] = max(10, int(interval))
        w["next_due"] = 0.0


# ============================ loops ============================
async def w1_loop():
    while True:
        try:
            if W1["enabled"]:
                W1["running"] = True
                W1["phase"] = "扫描工厂事件"
                await w1_discover()
                W1["phase"] = "补全创建者画像"
                await w1_enrich_creators()
                W1["phase"] = "空闲"
        except Exception as e:  # noqa: BLE001
            STATUS["error"] = "w1: %s" % e
        finally:
            W1["running"] = False
        await asyncio.sleep(max(2, W1["interval"]))


async def w2_loop():
    await asyncio.sleep(8)
    while True:
        try:
            if W2["enabled"]:
                await w2_refresh()
        except Exception as e:  # noqa: BLE001
            STATUS["error"] = "w2: %s" % e
        # self-heal: retry soon while the window is still empty, else use the interval
        await asyncio.sleep(15 if not W2["rank"] else max(15, W2["interval"]))


async def w3_loop():
    while True:
        try:
            if W3["enabled"] and W3["watch"]:
                await w3_refresh_due()
        except Exception as e:  # noqa: BLE001
            STATUS["error"] = "w3: %s" % e
        await asyncio.sleep(W3_TICK)


async def w4_loop():
    await asyncio.sleep(12)                       # let W1/W2 warm up first (shares the rate budget)
    while True:
        try:
            if W4["enabled"]:
                await w4_refresh()
        except Exception as e:  # noqa: BLE001
            STATUS["error"] = "w4: %s" % e
        # self-heal like W2: retry soon while still empty, else honour the interval
        await asyncio.sleep(15 if not W4["rank"] else max(30, W4["interval"]))

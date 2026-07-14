"""Shared helpers + small infra: caches, rate limiter, error log, state persistence.

Consolidates what used to be duplicated across clawby/sources/monitors/analyze.
"""
import asyncio
import json
import os
import time
from collections import OrderedDict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")


# ---------- tiny formatters ----------
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def i(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def get(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def dig(x):
    """Unwrap GMGN's {code,data,message} envelope down to the payload."""
    while isinstance(x, dict) and (set(x.keys()) & {"code", "data", "message"}):
        x = x.get("data")
    return x


def x_items(d):
    """Pull the list out of an x_search reply, whatever key it uses."""
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("tweets", "results", "items", "data"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


async def retry(fn, tries=4, ok=bool, base=0.5):
    """Await fn() until ok(result) is truthy, backing off; returns last result."""
    r = None
    for n in range(tries):
        r = await fn()
        if ok(r):
            return r
        await asyncio.sleep(base * (n + 1))
    return r


# ---------- caches ----------
class Capped(OrderedDict):
    """dict with a max size; drops the oldest entry on overflow (LRU-on-write)."""

    def __init__(self, cap=10000):
        super().__init__()
        self.cap = cap

    def __setitem__(self, k, v):
        if k in self:
            super().__delitem__(k)
        super().__setitem__(k, v)
        while len(self) > self.cap:
            super().__delitem__(next(iter(self)))


class TTLCache:
    """key -> value with per-entry expiry; get() returns None when missing/expired."""

    def __init__(self, ttl, cap=5000):
        self.ttl = ttl
        self.cap = cap
        self._d = OrderedDict()

    def get(self, k):
        v = self._d.get(k)
        if not v:
            return None
        val, exp = v
        if time.monotonic() > exp:
            self._d.pop(k, None)
            return None
        return val

    def set(self, k, val):
        self._d[k] = (val, time.monotonic() + self.ttl)
        while len(self._d) > self.cap:
            self._d.popitem(last=False)


class RateLimiter:
    """Space request starts to at most `per_sec` per second (protects the API quota)."""

    def __init__(self, per_sec):
        self._min = 1.0 / per_sec
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self):
        async with self._lock:
            gap = time.monotonic() - self._last
            if gap < self._min:
                await asyncio.sleep(self._min - gap)
            self._last = time.monotonic()


# ---------- error ring buffer ----------
_ERRLOG = deque(maxlen=200)


def logerr(msg):
    _ERRLOG.append({"t": now_iso(), "msg": str(msg)[:300]})


def errors():
    return list(_ERRLOG)


# ---------- state persistence ----------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:  # noqa: BLE001
        logerr("save_state: %s" % e)

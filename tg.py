"""Telegram bot alerts for high AI scores.

The app itself runs proxy-free, but Telegram is usually only reachable through the
proxy run.sh saved (SAVED_HTTPS_PROXY / SAVED_ALL_PROXY), so the TG client uses it.
Config is admin-editable at runtime and persisted in state.json.
"""
import asyncio
import os

import httpx

CONFIG = {"token": "", "chat_id": "", "threshold": 80, "enabled": False}
NOTIFIED = set()          # CAs already alerted at/above threshold (avoids repeat spam)


def get_config():
    return dict(CONFIG)


def public_config():
    """Config for the UI — token masked, never sent back in full."""
    c = dict(CONFIG)
    tok = CONFIG.get("token") or ""
    c["token"] = (tok[:10] + "…" + tok[-4:]) if len(tok) > 16 else ("****" if tok else "")
    c["has_token"] = bool(tok)
    return c


def set_config(token=None, chat_id=None, threshold=None, enabled=None):
    if token is not None:
        token = token.strip()
        if token and "…" not in token:            # ignore the masked value coming back
            CONFIG["token"] = token
    if chat_id is not None:
        CONFIG["chat_id"] = str(chat_id).strip()
    if threshold is not None:
        try:
            CONFIG["threshold"] = max(1, min(100, int(threshold)))
        except (TypeError, ValueError):
            pass
    if enabled is not None:
        CONFIG["enabled"] = bool(enabled)


def _proxy():
    return (os.environ.get("SAVED_HTTPS_PROXY") or os.environ.get("SAVED_HTTP_PROXY")
            or os.environ.get("SAVED_ALL_PROXY") or None)


async def _api(method, token, **params):
    kw = {"timeout": 15.0}
    proxy = _proxy()
    if proxy:
        kw["proxy"] = proxy
    last = None
    for attempt in range(3):                      # the local proxy occasionally drops the TLS tunnel
        try:
            async with httpx.AsyncClient(**kw) as c:
                r = await c.post("https://api.telegram.org/bot%s/%s" % (token, method), json=params)
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            await asyncio.sleep(0.5 * (attempt + 1))
    return {"ok": False, "description": repr(last)}


async def send(text, token=None, chat_id=None):
    token = token or CONFIG["token"]
    chat_id = chat_id or CONFIG["chat_id"]
    if not token or not chat_id:
        return False, "缺少 token 或 chat_id"
    j = await _api("sendMessage", token, chat_id=chat_id, text=text,
                   parse_mode="HTML", disable_web_page_preview=True)
    return bool(j.get("ok")), (j.get("description") or "ok")


async def get_chats(token=None):
    """Recent chat ids the bot can see (the user must message the bot first)."""
    token = token or CONFIG["token"]
    if not token:
        return []
    j = await _api("getUpdates", token)
    out, seen = [], set()
    for u in (j.get("result") or []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        ch = m.get("chat") or {}
        cid = ch.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            out.append({"id": cid, "type": ch.get("type"),
                        "name": ch.get("title") or ch.get("username") or ch.get("first_name")})
    return out


def _format(t):
    ca = t.get("ca") or ""
    mcap = t.get("mcap")
    mc = ("$%.2fM" % (mcap / 1e6)) if mcap and mcap >= 1e6 else (("$%.0fK" % (mcap / 1e3)) if mcap else "?")
    return ("🚀 <b>%s</b> · AI Score <b>%s/100</b>\n"
            "%s · mcap %s\n%s\n<code>%s</code>\n"
            "https://robinhoodchain.blockscout.com/token/%s") % (
        t.get("symbol") or "?", t.get("score"), t.get("platform") or "?", mc,
        (t.get("rationale") or "")[:400], ca, ca)


async def notify_score(t):
    """Alert for a freshly-scored token if enabled + score >= threshold (once per CA)."""
    if not (CONFIG["enabled"] and CONFIG["token"] and CONFIG["chat_id"]):
        return
    ca, score = t.get("ca"), t.get("score")
    if not ca or score is None:
        return
    if score >= CONFIG["threshold"]:
        if ca in NOTIFIED:
            return
        NOTIFIED.add(ca)
        ok, _ = await send(_format(t))
        if not ok:
            NOTIFIED.discard(ca)          # let it retry next round
    else:
        NOTIFIED.discard(ca)              # dropped below → re-alert if it climbs back

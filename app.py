"""Robinhood meme monitor — multi-window demo. FastAPI app + state persistence."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

import analyze
import clawby
import monitors as M
import tg
import util
import wallets

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------- state persistence ----------------
def collect_state():
    return {
        "config": {
            "concurrency": clawby.get_concurrency(),
            "lang": analyze.get_lang(),
            "engine": analyze.get_engine(),
            "report_dir": analyze.get_report_dir(),
            "w1_interval": M.W1["interval"], "w2_interval": M.W2["interval"],
            "w4_interval": M.W4["interval"], "w5_interval": M.W5["interval"],
            "w1_enabled": M.W1["enabled"], "w2_enabled": M.W2["enabled"], "w3_enabled": M.W3["enabled"],
            "w4_enabled": M.W4["enabled"], "w5_enabled": M.W5["enabled"],
        },
        "platforms": {p["name"]: p.get("enabled", False) for p in M.PLATFORMS},
        "fields": M.fields_state(),
        "scores": {ca: {"score": t.get("score"), "rationale": t.get("rationale"),
                        "scored_at": t.get("scored_at"), "symbol": t.get("symbol"),
                        "name": t.get("name"), "icon": t.get("icon"), "platform": t.get("platform"),
                        "mcap": t.get("mcap"), "volume24": t.get("volume24"), "holders": t.get("holders")}
                   for ca, t in M.W5["tokens"].items() if t.get("score") is not None},
        "telegram": tg.get_config(),
        "tg_notified": list(tg.NOTIFIED),
        "watch": {ca: {"interval": w["interval"], "added": w.get("added"),
                       "symbol": w.get("symbol"), "icon": w.get("icon")}
                  for ca, w in M.W3["watch"].items()},
        "jobs": {ca: {"report": j.get("report"), "finished": j.get("finished")}
                 for ca, j in analyze.JOBS.items() if j.get("status") == "done" and j.get("report")},
    }


def apply_state(st):
    cfg = st.get("config") or {}
    if "concurrency" in cfg:
        clawby.set_concurrency(cfg["concurrency"])
    if cfg.get("lang"):
        analyze.set_lang(cfg["lang"])
    if cfg.get("engine"):
        analyze.set_engine(cfg["engine"])
    if cfg.get("report_dir"):
        analyze.set_report_dir(cfg["report_dir"])
    for win in ("w1", "w2", "w4", "w5"):
        if win + "_interval" in cfg:
            M.set_interval(win, cfg[win + "_interval"])
    # windows boot PAUSED (avoids an initial concurrency spike) — the user turns on
    # up to M.MAX_ACTIVE of them. Enabled state is intentionally NOT persisted.
    for win in M.ALL_WINDOWS:
        M.set_enabled(win, False)
    tgc = st.get("telegram") or {}
    tg.set_config(token=tgc.get("token"), chat_id=tgc.get("chat_id"),
                  threshold=tgc.get("threshold"), enabled=tgc.get("enabled"))
    tg.NOTIFIED.update(st.get("tg_notified") or [])
    for name, en in (st.get("platforms") or {}).items():
        M.set_platform_enabled(name, en)
    for win, fs in (st.get("fields") or {}).items():
        for name, val in (fs or {}).items():
            M.set_field(win, name, val)
    for ca, s in (st.get("scores") or {}).items():
        M.W5["tokens"][ca.lower()] = {
            "ca": ca.lower(), "symbol": s.get("symbol"), "name": s.get("name"), "icon": s.get("icon"),
            "platform": s.get("platform"), "mcap": s.get("mcap"), "volume24": s.get("volume24"),
            "holders": s.get("holders"), "score": s.get("score"), "rationale": s.get("rationale"),
            "scored_at": s.get("scored_at"), "scoring": False, "error": None}
    for ca, w in (st.get("watch") or {}).items():
        M.W3["watch"][ca.lower()] = {"interval": max(10, int(w.get("interval", 60))), "next_due": 0.0,
                                     "data": {}, "added": w.get("added") or M.now(),
                                     "symbol": w.get("symbol"), "icon": w.get("icon")}
    for ca, j in (st.get("jobs") or {}).items():
        if j.get("report") and os.path.exists(j["report"]):
            analyze.JOBS[ca.lower()] = {"status": "done", "step": "完成", "report": j["report"],
                                        "finished": j.get("finished"), "started": None, "error": None}


def _save():
    util.save_state(collect_state())


async def _save_loop():
    while True:
        await asyncio.sleep(30)
        _save()


@asynccontextmanager
async def lifespan(_app):
    apply_state(util.load_state())
    tasks = [asyncio.create_task(M.w1_loop()), asyncio.create_task(M.w2_loop()),
             asyncio.create_task(M.w3_loop()), asyncio.create_task(M.w4_loop()),
             asyncio.create_task(M.w5_loop()), asyncio.create_task(M.w5_score_loop()),
             asyncio.create_task(_save_loop())]
    try:
        yield
    finally:
        _save()
        for t in tasks:
            t.cancel()


app = FastAPI(title="Robinhood Meme Monitor", lifespan=lifespan)


# ---------------- read endpoints ----------------
@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "dashboard.html"))


@app.get("/api/w1")
async def api_w1():
    rows = sorted(M.W1["tokens"].values(),
                  key=lambda t: (t["created_ts"] or 0, t["created_block"]), reverse=True)
    return JSONResponse(rows)


@app.get("/api/w2")
async def api_w2():
    return JSONResponse([M.W2["tokens"][ca] for ca in M.W2["rank"] if ca in M.W2["tokens"]])


@app.get("/api/w4")
async def api_w4():
    return JSONResponse([M.W4["tokens"][ca] for ca in M.W4["rank"] if ca in M.W4["tokens"]])


@app.get("/api/w5")
async def api_w5():
    toks = M.W5["tokens"]
    order = M.W5["rank"] or list(toks.keys())          # fall back to all tokens if candidates not fetched yet
    seen = set()
    rows = []
    for ca in order:
        if ca in toks and ca not in seen:
            seen.add(ca)
            rows.append(toks[ca])
    for ca, tkn in toks.items():                        # include scored tokens not in the rank list
        if ca not in seen:
            rows.append(tkn)
    rows.sort(key=lambda t: (t.get("score") is None, -(t.get("score") or 0)))  # scored first, high→low
    return JSONResponse(rows)


@app.get("/api/w3")
async def api_w3():
    out = []
    for ca, w in M.W3["watch"].items():
        row = {"ca": ca, "symbol": w.get("symbol"), "icon": w.get("icon"),
               "interval": w["interval"], "added": w["added"]}
        row.update(w.get("data") or {})
        out.append(row)
    return JSONResponse(out)


@app.get("/api/status")
async def api_status():
    return {
        "latest_block": M.STATUS.get("latest_block"),
        "error": M.STATUS.get("error"),
        "errors": util.errors()[-8:],
        "api_calls": clawby.call_count(),
        "wallet_cache": wallets.cache_size(),
        "concurrency": clawby.get_concurrency(),
        "report_dir": analyze.get_report_dir(),
        "lang": analyze.get_lang(),
        "engine": analyze.get_engine(),
        "max_active": M.MAX_ACTIVE,
        "rate_banned": clawby.banned_for(),
        "telegram": tg.public_config(),
        "platforms": M.platforms_state(),
        "w1": {"enabled": M.W1["enabled"], "count": len(M.W1["tokens"]), "last": M.W1["last"],
               "interval": M.W1["interval"], "running": M.W1["running"], "phase": M.W1["phase"]},
        "w2": {"enabled": M.W2["enabled"], "count": len(M.W2["rank"]), "last": M.W2["last"],
               "interval": M.W2["interval"], "running": M.W2["running"], "phase": M.W2["phase"],
               "done": M.W2["done"], "total": M.W2["total"]},
        "w3": {"enabled": M.W3["enabled"], "count": len(M.W3["watch"]), "last": M.W3["last"],
               "running": M.W3["running"], "phase": M.W3["phase"]},
        "w4": {"enabled": M.W4["enabled"], "count": len(M.W4["rank"]), "last": M.W4["last"],
               "interval": M.W4["interval"], "running": M.W4["running"], "phase": M.W4["phase"],
               "done": M.W4["done"], "total": M.W4["total"]},
        "w5": {"enabled": M.W5["enabled"], "count": len(M.W5["rank"]), "last": M.W5["last"],
               "interval": M.W5["interval"], "running": M.W5["running"], "phase": M.W5["phase"],
               "scoring": M.W5["scoring"], "pool": len(M.W5["tokens"]),
               "scoring_sym": (M.W5["tokens"].get(M.W5["scoring"]) or {}).get("symbol") if M.W5["scoring"] else None,
               "scored": sum(1 for t in M.W5["tokens"].values() if t.get("score") is not None)},
        "fields": M.fields_state(),
    }


# ---------------- control endpoints (persist on change) ----------------
@app.post("/api/window/{name}/toggle")
async def api_toggle(name: str, req: Request):
    body = await req.json()
    want = bool(body.get("enabled", True))
    w = M._wmap().get(name)
    if want and w is not None and not w["enabled"] and M.enabled_count() >= M.MAX_ACTIVE:
        return {"ok": False, "reason": "limit", "limit": M.MAX_ACTIVE}
    ok = M.set_enabled(name, want)
    _save()
    return {"ok": ok, "enabled": want}


@app.post("/api/window/{name}/interval")
async def api_interval(name: str, req: Request):
    body = await req.json()
    ok = M.set_interval(name, body.get("interval", 5))
    _save()
    return {"ok": ok}


@app.post("/api/config")
async def api_config(req: Request):
    body = await req.json()
    if "concurrency" in body:
        clawby.set_concurrency(body["concurrency"])
    if body.get("report_dir"):
        analyze.set_report_dir(body["report_dir"])
    if body.get("lang"):
        analyze.set_lang(body["lang"])
    if body.get("engine"):
        analyze.set_engine(body["engine"])
    _save()
    return {"ok": True, "concurrency": clawby.get_concurrency(),
            "report_dir": analyze.get_report_dir(), "lang": analyze.get_lang(),
            "engine": analyze.get_engine()}


@app.post("/api/platform/toggle")
async def api_platform_toggle(req: Request):
    body = await req.json()
    ok = M.set_platform_enabled(body.get("name"), body.get("enabled", True))
    _save()
    return {"ok": ok}


@app.post("/api/field/toggle")
async def api_field_toggle(req: Request):
    body = await req.json()
    ok = M.set_field(body.get("win"), body.get("name"), body.get("enabled", True))
    _save()
    return {"ok": ok}


# ---------------- Telegram alerts ----------------
@app.post("/api/tg/config")
async def api_tg_config(req: Request):
    body = await req.json()
    tg.set_config(token=body.get("token"), chat_id=body.get("chat_id"),
                  threshold=body.get("threshold"), enabled=body.get("enabled"))
    _save()
    return {"ok": True, "telegram": tg.public_config()}


@app.post("/api/tg/test")
async def api_tg_test():
    ok, msg = await tg.send("✅ Clawby Memescan — 测试通知 / test alert. 配置成功。")
    return {"ok": ok, "msg": msg}


@app.get("/api/tg/chats")
async def api_tg_chats():
    return {"chats": await tg.get_chats()}


# ---------------- watchlist ----------------
@app.post("/api/watch")
async def api_watch(req: Request):
    body = await req.json()
    ca = body.get("ca")
    if not ca:
        return JSONResponse({"error": "ca required"}, status_code=400)
    M.add_watch(ca, body.get("interval", 60))
    _save()
    return {"ok": True}


@app.delete("/api/watch/{ca}")
async def api_unwatch(ca: str):
    M.remove_watch(ca)
    _save()
    return {"ok": True}


@app.post("/api/watch/{ca}/interval")
async def api_watch_interval(ca: str, req: Request):
    body = await req.json()
    M.set_watch_interval(ca, body.get("interval", 60))
    _save()
    return {"ok": True}


# ---------------- deep analysis ----------------
@app.post("/api/analyze")
async def api_analyze(req: Request):
    body = await req.json()
    ca = (body.get("ca") or "").lower()
    if not ca:
        return JSONResponse({"error": "ca required"}, status_code=400)
    if analyze.JOBS.get(ca, {}).get("status") in ("gathering", "analyzing", "rendering"):
        return {"ok": True, "status": analyze.JOBS[ca]["status"], "note": "already running"}
    asyncio.create_task(analyze.run_analysis(ca))
    return {"ok": True, "status": "started"}


@app.get("/api/analyze/{ca}")
async def api_analyze_status(ca: str):
    j = analyze.JOBS.get(ca.lower())
    if not j:
        return {"status": "none"}
    return {**j, "has_report": bool(j.get("report") and os.path.exists(j["report"]))}


@app.get("/api/report/{ca}")
async def api_report(ca: str):
    j = analyze.JOBS.get(ca.lower())
    if j and j.get("report") and os.path.exists(j["report"]):
        return FileResponse(j["report"], media_type="application/pdf",
                            filename=os.path.basename(j["report"]))
    return JSONResponse({"error": "no report"}, status_code=404)

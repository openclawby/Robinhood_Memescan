"""Robinhood meme monitor — multi-window demo. FastAPI app + state persistence."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

import analyze
import clawby
import monitors as M
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
            "w1_interval": M.W1["interval"], "w2_interval": M.W2["interval"], "w4_interval": M.W4["interval"],
            "w1_enabled": M.W1["enabled"], "w2_enabled": M.W2["enabled"],
            "w3_enabled": M.W3["enabled"], "w4_enabled": M.W4["enabled"],
        },
        "platforms": {p["name"]: p.get("enabled", False) for p in M.PLATFORMS},
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
    for win in ("w1", "w2", "w4"):
        if win + "_interval" in cfg:
            M.set_interval(win, cfg[win + "_interval"])
    # windows always boot ENABLED — a monitor should monitor on launch. A persisted
    # "paused" state is intentionally NOT restored (pause is a runtime-only action);
    # otherwise an earlier pause would silently keep every window off after a restart.
    for win in ("w1", "w2", "w3", "w4"):
        M.set_enabled(win, True)
    for name, en in (st.get("platforms") or {}).items():
        M.set_platform_enabled(name, en)
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
    }


# ---------------- control endpoints (persist on change) ----------------
@app.post("/api/window/{name}/toggle")
async def api_toggle(name: str, req: Request):
    body = await req.json()
    ok = M.set_enabled(name, body.get("enabled", True))
    _save()
    return {"ok": ok, "enabled": body.get("enabled")}


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

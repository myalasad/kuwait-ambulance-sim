"""Live dashboard server: FastAPI + WebSocket streaming the running SUMO
simulation onto a Leaflet map of downtown Kuwait City.

Run with:  python run_live.py   (then open http://127.0.0.1:8642)

All TraCI calls happen inside the single simulation loop task, so the TraCI
connection is never touched concurrently; WebSocket handlers only enqueue
commands.
"""
import asyncio
import json
import os
import sys
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.config import SimConfig  # noqa: E402
from sim.runner import Simulation  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Hub:
    def __init__(self):
        self.sim = None
        self.clients = set()
        self.commands = asyncio.Queue()
        self.speed = 1.0
        self.paused = False
        self.network_cache = None
        self.error = None          # fatal sim error message, shown to clients
        self.last_snap = None

    def start_sim(self, preemption=True):
        cfg = SimConfig()
        if getattr(self, "hour", None) is not None:
            cfg.start_hour = int(self.hour) % 24
        sim = Simulation(ROOT, cfg, preemption=preemption)
        sim.start()                # raises on failure; self.sim stays valid
        self.sim = sim
        self.network_cache = self.sim.network_payload()
        self.error = None

    # ------------------------------------------------------------- sim loop

    async def run(self):
        try:
            self.start_sim()
        except Exception as exc:
            self.error = f"Simulation failed to start: {exc}"
            print(self.error, file=sys.stderr)
        step_len = self.sim.cfg.step_length if self.sim else 0.5
        heartbeat = 0.0
        while True:
            t0 = time.perf_counter()
            await self._drain_commands()
            if self.error or self.paused or self.sim is None:
                # keep clients informed: heartbeat the last frame (or the
                # error) so pause/reset/errors are visible everywhere
                if time.perf_counter() - heartbeat > 0.5:
                    heartbeat = time.perf_counter()
                    frame = dict(self.last_snap) if self.last_snap else {"t": 0}
                    frame.update({"paused": self.paused, "speed": self.speed,
                                  "error": self.error})
                    await self._broadcast(frame)
                await asyncio.sleep(0.1)
                continue
            try:
                self.sim.step()
                snap = self.sim.snapshot()
            except Exception as exc:
                self.error = f"Simulation died: {exc} — use Reset to restart"
                print(self.error, file=sys.stderr)
                continue
            snap["speed"] = self.speed
            snap["paused"] = self.paused
            snap["error"] = None
            self.last_snap = snap
            await self._broadcast(snap)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0.005, step_len / self.speed - elapsed))

    async def _drain_commands(self):
        while not self.commands.empty():
            cmd = await self.commands.get()
            try:
                self._apply(cmd)
            except Exception as exc:  # surface errors to the event log
                if self.sim is not None:
                    self.sim.log_event(f"Command failed: {exc}")

    def _apply(self, cmd):
        kind = cmd.get("cmd")
        if kind == "dispatch":
            origin = cmd.get("origin")          # hospital name / [lat,lon] / None
            dest = cmd.get("dest")              # [lat,lon] / None
            if isinstance(origin, list):
                origin = tuple(origin)
            if isinstance(dest, list):
                dest = tuple(dest)
            self.sim.dispatch(origin, dest)
        elif kind == "preemption":
            self.sim.set_preemption(bool(cmd.get("on", True)))
        elif kind == "decide":
            self.sim.controller.decide(cmd.get("tls"), cmd.get("amb"),
                                       who=cmd.get("who", "operator"))
        elif kind == "lights":
            self.sim.dispatcher.set_lights(cmd.get("amb"),
                                           bool(cmd.get("on", True)),
                                           self.sim.time,
                                           who=cmd.get("who", "operator"))
        elif kind == "speed":
            self.speed = min(16.0, max(0.25, float(cmd.get("value", 1.0))))
        elif kind == "pause":
            self.paused = True
        elif kind == "resume":
            self.paused = False
        elif kind == "reset":
            if cmd.get("hour") is not None:
                self.hour = int(cmd["hour"]) % 24
            keep = self.sim.controller.enabled if self.sim else True
            if self.sim is not None:
                self.sim.close()
                self.sim = None
            self.last_snap = None
            try:
                self.start_sim(preemption=keep)
            except Exception as exc:
                self.error = f"Restart failed: {exc} — try Reset again"
                raise

    async def _broadcast(self, snap):
        if not self.clients:
            return
        payload = json.dumps(snap)
        dead = []
        for ws in list(self.clients):
            try:
                # a stalled client must not freeze the sim for everyone
                await asyncio.wait_for(ws.send_text(payload), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()
app = FastAPI(title="Kuwait Ambulance Green-Wave Simulation")


@app.on_event("startup")
async def _startup():
    task = asyncio.get_event_loop().create_task(hub.run())
    app.state.sim_task = task  # keep a reference; log if it ever dies
    task.add_done_callback(
        lambda t: print(f"sim loop exited: {t.exception()}", file=sys.stderr)
        if not t.cancelled() and t.exception() else None)


def _page(name):
    return FileResponse(os.path.join(STATIC, name),
                        headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index():
    return _page("index.html")


@app.get("/operations")
async def operations_page():
    return _page("operations.html")


@app.get("/navigation")
async def navigation_page():
    return _page("navigation.html")


@app.get("/protocol")
async def protocol_page():
    return _page("protocol.html")


@app.get("/driver")
async def driver_page():
    return _page("driver.html")


@app.get("/how")
async def how_page():
    return _page("how.html")


@app.get("/api/operations")
async def api_operations(since: int = 0):
    if hub.sim is None or hub.sim.ops is None:
        return JSONResponse({"events": [], "seq": 0})
    events = hub.sim.ops.since(since)
    seq = hub.sim.ops.seq
    return JSONResponse({"events": events, "seq": seq,
                         "pending": hub.sim.controller.pending_decisions(),
                         "clock": hub.sim.clock()})


@app.get("/api/cases")
async def api_cases():
    if hub.sim is None or hub.sim.ops is None:
        return JSONResponse({"cases": []})
    return JSONResponse({"cases": hub.sim.ops.case_list()})


@app.get("/api/navigation")
async def api_navigation():
    if hub.sim is None:
        return JSONResponse({"ambulances": []})
    return JSONResponse({
        "ambulances": hub.sim.dispatcher.navigation(),
        "tls_status": hub.sim.controller.status(),
        "clock": hub.sim.clock(),
    })


@app.get("/api/analysis")
async def api_analysis():
    if hub.sim is None or hub.sim.metrics is None:
        return JSONResponse({"runs": []})
    runs = hub.sim.metrics.analysis[-30:]
    agg = None
    if runs:
        saved = [r["est_without_s"] - r["actual_s"] for r in runs]
        agg = {
            "runs": len(runs),
            "mean_actual_s": round(sum(r["actual_s"] for r in runs) / len(runs), 1),
            "mean_without_s": round(sum(r["est_without_s"] for r in runs) / len(runs), 1),
            "mean_saved_s": round(sum(saved) / len(saved), 1),
            "mean_saved_pct": round(100 * sum(saved) /
                                    max(1, sum(r["est_without_s"] for r in runs)), 1),
        }
    return JSONResponse({"runs": runs, "aggregate": agg})


@app.post("/api/command")
async def api_command(cmd: dict):
    """Same command surface as the WebSocket, for the auxiliary pages
    (operator decisions, lights toggles)."""
    await hub.commands.put(cmd)
    return JSONResponse({"queued": True})


@app.get("/api/network")
async def network():
    for _ in range(150):                  # sim still booting: wait up to 30 s
        if hub.network_cache is not None:
            return JSONResponse(hub.network_cache)
        if hub.error:
            break
        await asyncio.sleep(0.2)
    return JSONResponse({"error": hub.error or "simulation failed to start"},
                        status_code=503)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                await hub.commands.put(json.loads(msg))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)

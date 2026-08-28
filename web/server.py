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
        if getattr(self, "scenario", None):
            cfg = SimConfig(scenario=self.scenario)
            if getattr(self, "hour", None) is not None:
                cfg.start_hour = int(self.hour) % 24
        if getattr(self, "day_type", None) in ("weekday", "weekend"):
            cfg.day_type = self.day_type
        if getattr(self, "traffic_level", None) in ("easy", "medium", "extreme"):
            cfg.traffic_level = self.traffic_level
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
        next_tick = time.perf_counter()
        while True:
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
            if not getattr(self.sim, "_warmed", False):
                await self._warmup()
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
            # steady cadence: schedule against an absolute clock so frame
            # delivery has no cumulative drift or sleep jitter
            next_tick += step_len / self.speed
            nowp = time.perf_counter()
            if next_tick < nowp - 1.0:
                next_tick = nowp          # fell far behind: resync
            await asyncio.sleep(max(0.002, next_tick - nowp))

    async def _warmup(self):
        """Fast-forward the first minutes of city time at full speed so the
        network is already flowing when the first frame is served."""
        sim = self.sim
        target = float(getattr(sim.cfg, "warmup_s", 0) or 0)
        try:
            while sim.time < target:
                for _ in range(20):
                    sim.step()
                frame = {"t": sim.time, "paused": False, "speed": self.speed,
                         "error": None,
                         "warmup": {"done": round(sim.time),
                                    "total": round(target)}}
                await self._broadcast(frame)
                await asyncio.sleep(0)          # keep the server responsive
        except Exception as exc:
            self.error = f"Warm-up failed: {exc} — use Reset"
            print(self.error, file=sys.stderr)
        sim._warmed = True

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
            if cmd.get("scenario"):
                self.scenario = str(cmd["scenario"])
            if cmd.get("day_type"):
                self.day_type = str(cmd["day_type"])
            if cmd.get("traffic_level"):
                self.traffic_level = str(cmd["traffic_level"])
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
    pending = []
    for pd in hub.sim.controller.pending_decisions():
        pd = dict(pd); pd["tls_name"] = hub.sim.places.jn(pd["tls"])
        pending.append(pd)
    return JSONResponse({"events": events, "seq": seq,
                         "pending": pending,
                         "clock": hub.sim.clock()})


@app.get("/api/cases")
async def api_cases():
    if hub.sim is None or hub.sim.ops is None:
        return JSONResponse({"cases": []})
    cases = []
    for c in hub.sim.ops.case_list():
        c = dict(c)
        if c["kind"] == "P":
            c["subject_name"] = hub.sim.places.jn(c["subject"])
        elif c["kind"] == "D":
            c["subject_name"] = hub.sim.places.jn(c["subject"])
        else:
            c["subject_name"] = c["subject"]
        cases.append(c)
    return JSONResponse({"cases": cases})


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


_rag = {"index": None, "size": 0}


def _rag_index():
    """Build (or refresh) the copilot index; cheap, so refresh when the
    operations log has grown materially — the corpus feeds itself too."""
    from rag.ingest import build_corpus
    from rag.index import Index
    path = os.path.join(ROOT, "data", "operations.jsonl")
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if _rag["index"] is None or size > _rag["size"] * 1.2 + 4096:
        docs = build_corpus(ROOT)
        if hub.sim is not None and hub.sim.markov is not None:
            s = hub.sim.markov.summary()
            lines = [f"Live Markov traffic analytics "
                     f"({s['total_observations']} observations, "
                     f"{s['loaded_from_previous_sessions']} carried over from "
                     f"previous sessions; DTMC+CTMC on "
                     f"{s['monitored_edges']} corridors):"]
            for r in s["top_corridors"]:
                lines.append(
                    f"corridor {r['edge']}: now {r['state_now']}, long-run "
                    f"congested share {r['congested_share']:.0%}, P(jam in "
                    f"5 min) {r['p_jam_5min']:.0%}, stationary "
                    f"{r['stationary']}")
            if s.get("network_major_stationary"):
                lines.append(f"major-road network stationary distribution: "
                             f"{s['network_major_stationary']}")
            docs.append({"id": "markov:live", "type": "markov",
                         "title": "Markov traffic analytics (live)",
                         "text": "\n".join(lines),
                         "meta": {"ambs": [], "cases": [],
                                  "tls": [r["edge"] for r in
                                          s["top_corridors"]],
                                  "kinds": ["markov"]}})
        _rag["index"] = Index(docs)
        _rag["size"] = size
    return _rag["index"]


@app.post("/api/ask")
async def api_ask(body: dict):
    """Operations Copilot: retrieval-grounded Q&A over the system's records.
    Runs in a worker thread so the simulation loop never blocks on it."""
    question = str(body.get("question", ""))[:1000].strip()
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)

    def work():
        from rag.answer import answer
        index = _rag_index()
        docs = index.search(question, k=6)
        return answer(question, docs)

    result = await asyncio.get_event_loop().run_in_executor(None, work)
    return JSONResponse(result)


@app.get("/api/markov")
async def api_markov():
    if hub.sim is None or hub.sim.markov is None:
        return JSONResponse({"error": "simulation not running"},
                            status_code=503)
    return JSONResponse(hub.sim.markov.summary())


@app.get("/copilot")
async def copilot_page():
    return _page("copilot.html")


@app.get("/api/markov/corridor")
async def api_markov_corridor(edge: str):
    if hub.sim is None or hub.sim.markov is None:
        return JSONResponse({"error": "simulation not running"}, status_code=503)
    d = hub.sim.markov.corridor_detail(edge)
    if d is None:
        return JSONResponse({"error": "no history for that corridor"},
                            status_code=404)
    return JSONResponse(d)


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

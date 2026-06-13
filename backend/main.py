"""FastAPI server for the maritime DSS prototype.

Runs three background tasks:
  * the simulation tick loop (advances vehicle state),
  * the scheduled-event loop (fires SCENARIO_B over time),
  * the WebSocket broadcaster (pushes a full snapshot every second).

REST endpoints expose snapshots, dead-reckoning estimates, manual event
injection and task assignment.

Run with:  uvicorn main:app --reload   (from the backend/ folder)
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from simulator import Simulator
from events import EventScheduler
from weather import WeatherService
from ais import AISService

sim = Simulator()
scheduler = EventScheduler(sim)
weather = WeatherService()
ais = AISService()
# Expose live providers to the simulator so they ride along in each snapshot.
sim.weather = weather
sim.ais = ais


class ConnectionManager:
    """Tracks active WebSocket clients for broadcast."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _sim_loop():
    """Advance the simulator and fire scheduled events at high cadence."""
    while True:
        sim.tick()
        scheduler.step()
        await asyncio.sleep(0.25)


async def _broadcast_loop():
    """Push a full snapshot to all WebSocket clients once per second."""
    while True:
        await manager.broadcast(sim.snapshot())
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_sim_loop()),
        asyncio.create_task(_broadcast_loop()),
        asyncio.create_task(weather.run()),
        asyncio.create_task(ais.run()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Maritime DSS Prototype", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send an immediate snapshot so the client doesn't wait up to 1s.
    await ws.send_json(sim.snapshot())
    try:
        while True:
            # We don't expect client messages; just keep the socket open.
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# --------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------- #
@app.get("/vehicles")
def get_vehicles():
    return list(sim.vehicles.values())


@app.get("/contacts")
def get_contacts():
    return sim.contacts


@app.get("/weather")
def get_weather():
    return weather.current or {"detail": "weather not yet available"}


@app.get("/ais")
def get_ais():
    return {"enabled": ais.enabled, "vessels": ais.vessels()}


@app.get("/estimate/{vehicle_id}")
def get_estimate(vehicle_id: str):
    est = sim.estimate_position(vehicle_id)
    if est is None:
        return {
            "vehicle_id": vehicle_id,
            "submerged": False,
            "detail": "Vehicle is not in acoustic blackout; live position available.",
        }
    return est


@app.post("/inject/{event_type}")
def inject_event(event_type: str):
    desc = scheduler.fire_by_type(event_type)
    if desc is None:
        return {"ok": False, "detail": f"Unknown event type '{event_type}'"}
    return {"ok": True, "event_type": event_type, "description": desc}


class AssignRequest(BaseModel):
    vehicle_id: str
    task: str
    lat: float | None = None
    lon: float | None = None


@app.post("/assign")
def assign(req: AssignRequest):
    v = sim.assign_task(req.vehicle_id, req.task, req.lat, req.lon)
    if v is None:
        return {"ok": False, "detail": f"Unknown vehicle '{req.vehicle_id}'"}
    return {
        "ok": True,
        "vehicle_id": req.vehicle_id,
        "current_task": v["current_task"],
        "waypoint": v["waypoint"],
    }


@app.get("/")
def root():
    return {"service": "Maritime DSS Prototype", "sim_time_sec": sim.sim_time_sec}

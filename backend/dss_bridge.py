"""Bridge: stream the perceived-sim picture into the teammate's DSS engine.

WHY THIS EXISTS
---------------
Your simulator (`perceived_sim.py` via `main.py`) is a *server* that broadcasts
one fat snapshot per second over ``ws://localhost:8000/ws``. The teammate's DSS
(`README_dss.md`) is a *different* server that expects each vehicle to connect
as a *client* and push small, strictly-validated per-vehicle messages to
``ws://localhost:8001/dss/ws/vehicles``. The two schemas do not match.

This module is the adapter between them. It connects to BOTH sockets, reads
your snapshot stream, and fans each snapshot out into the DSS message schema:

    perceived vehicle (normal)   -> heartbeat + telemetry
    perceived vehicle (blackout) -> link_state(expected_blackout)   [no telem]
    threat contact               -> event(unknown_contact)
    notable alert                -> event(...)

BACKTRACK-SAFE BY DESIGN
------------------------
It imports nothing from your backend and edits no existing file. It is a third
process you start only when you want to feed the DSS. To undo the integration:
stop this process, or ``git rm backend/dss_bridge.py``. Your sim, UI and the
``/ws`` data contract are completely unaffected either way.

RUN
---
    # 1. your sim          (port 8000)
    uvicorn main:app --reload
    # 2. teammate's DSS    (port 8001)
    uvicorn dss_backend.main:app --reload --port 8001
    # 3. this bridge
    python dss_bridge.py

Override either endpoint with env vars::

    SIM_WS_URL=ws://localhost:8000/ws  DSS_WS_URL=ws://host:8001/dss/ws/vehicles

Everything is mapped per `README_dss.md`; the DSS uses ``extra="forbid"`` so we
emit ONLY documented fields.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import requests
import websockets

SIM_WS_URL = os.environ.get("SIM_WS_URL", "ws://localhost:8000/ws")
DSS_WS_URL = os.environ.get("DSS_WS_URL", "ws://localhost:8001/dss/ws/vehicles")

# Reverse path (DSS decision -> sim execution). The DSS pushes commands on a
# command-output socket; we translate and POST them to the sim's /command. The
# command socket does not exist in README_dss.md yet — README_dss_commands.md
# documents the schema we expect the teammate to emit. Both endpoints are
# env-overridable so they can point at the real DSS / a test stub.
DSS_CMD_WS_URL = os.environ.get("DSS_CMD_WS_URL", "ws://localhost:8001/dss/ws/commands")
SIM_CMD_URL = os.environ.get("SIM_CMD_URL", "http://localhost:8000/command")

# Seconds of expected_blackout window advertised to the DSS when a vehicle goes
# silent. Roughly matches SCENARIO_A's ~25 min acoustic blackout; the DSS will
# show expected_blackout until this elapses, then late_contact. Tune to taste.
BLACKOUT_WINDOW_SEC = 1500

# Mission-defined low-battery (return-home) threshold sent in every telemetry.
BINGO_THRESHOLD_PCT = 15

KN_TO_MPS = 0.514444

# --------------------------------------------------------------------------- #
# Static mappings: your fleet  ->  the DSS's six fixed vehicles
# --------------------------------------------------------------------------- #
DSS_ID = {
    "UAV-1": "air_1", "UAV-2": "air_2",
    "USV-1": "surface_1", "USV-2": "surface_2",
    "UUV-1": "sub_1", "UUV-2": "sub_2",
}
DOMAIN_BY_TYPE = {"UAV": "air", "USV": "surface", "UUV": "subsurface"}
# link_type -> DSS communication_mode (radio/acoustic/satellite/cellular).
COMM_MODE = {"radio": "radio", "satellite": "satellite", "acoustic": "acoustic"}
# DSS requires this exact value per domain in every heartbeat.
INTERVAL_MS = {"air": 1000, "surface": 3000, "subsurface": 30000}

# Which sim capability strings power each DSS sensor slot.
SENSOR_SOURCES = {
    "camera": ("visual_ISR", "thermal_ISR"),
    "radar": ("surface_radar",),
    "sonar": ("active_sonar", "passive_sonar"),
}

# Reverse direction: DSS vehicle id -> sim id, and the DSS's command vocabulary
# -> the sim's /command action enum (the canonical contract in commands.py.ACTIONS).
SIM_ID = {v: k for k, v in DSS_ID.items()}     # "sub_1" -> "UUV-1"

# The canonical action names ARE the contract; the ideal is that the DSS emits
# these directly, making this map an identity we could drop. The extra aliases
# below (goto/dive/return_home/...) are PROVISIONAL guesses kept only so we're not
# blocked on exact naming before integration. AT INTEGRATION: replace them with
# the DSS's real action strings and delete the rest — don't carry dead synonyms.
DSS_ACTION_MAP = {
    # canonical (preferred — DSS should emit these)
    "reroute": "reroute", "reassign_task": "reassign_task",
    "set_depth": "set_depth", "set_altitude": "set_altitude",
    "surface": "surface", "submerge": "submerge",
    "hold": "hold", "resume": "resume", "rtb": "rtb",
    "abort": "abort", "set_status": "set_status",
    # provisional aliases — prune to the DSS's actual vocabulary on integration
    "goto": "reroute", "move_to": "reroute",
    "assign_task": "reassign_task", "tasking": "reassign_task",
    "dive": "set_depth", "climb": "set_altitude",
    "loiter": "hold", "station_keep": "hold",
    "return_home": "rtb", "return_to_base": "rtb", "cancel": "abort",
}


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #
def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _type_of(sim_id: str) -> str:
    return sim_id.split("-", 1)[0]  # "UAV-1" -> "UAV"


def _dss_identity(sim_id: str):
    """(dss_id, domain, comm_mode) for a sim vehicle id, or None if unknown."""
    dss_id = DSS_ID.get(sim_id)
    if dss_id is None:
        return None
    vtype = _type_of(sim_id)
    return dss_id, DOMAIN_BY_TYPE[vtype], None  # comm_mode filled from link_type


# --------------------------------------------------------------------------- #
# Field translators (sim vehicle dict -> DSS sub-objects)
# --------------------------------------------------------------------------- #
def _map_status(p: dict) -> str:
    """Sim status -> DSS telemetry status (active/idle/standby/fault/returning/offline)."""
    s = p.get("status")
    if s == "bingo":
        return "returning"
    if s == "lost_comms":
        return "offline"
    # "nominal" and "degraded" are both still operating; degradation is carried
    # in the per-sensor health below, not in the operating status.
    return "active"


def _position(p: dict, domain: str) -> dict:
    z = p.get("z_m", 0.0) or 0.0
    pos = {"lat": p["lat"], "lon": p["lon"], "alt": None, "depth": None}
    if domain == "air":
        pos["alt"] = round(z, 1)
    elif domain == "subsurface":
        pos["depth"] = round(max(0.0, -z), 1)  # DSS requires depth >= 0
    return pos


def _velocity(p: dict) -> dict:
    heading = round((p.get("heading", 0.0) or 0.0) % 360, 1)
    if heading >= 360.0:
        heading = 0.0
    return {
        "speed_mps": round((p.get("speed_knots", 0.0) or 0.0) * KN_TO_MPS, 3),
        "heading_deg": heading,
    }


def _battery(p: dict) -> dict:
    return {
        "percentage": _clamp(round(p.get("battery_pct", 0.0) or 0.0, 1), 0, 100),
        "bingo_threshold": BINGO_THRESHOLD_PCT,
    }


def _sensors(p: dict) -> dict:
    """Map the capability list + sensor_health onto camera/radar/sonar health.

    A failed capability is removed from `capabilities` by the sim but kept in
    `sensor_health` at 0.0 — so we can tell `fault` (was present, now 0) from
    `unavailable` (never fitted).
    """
    caps = set(p.get("capabilities", []))
    health = p.get("sensor_health", {})
    out = {}
    for slot, sources in SENSOR_SOURCES.items():
        if any(c in caps and (health.get(c, 1.0) or 0.0) > 0 for c in sources):
            out[slot] = "ok"
        elif any(c in health and (health.get(c, 1.0) or 0.0) == 0 for c in sources):
            out[slot] = "fault"
        else:
            out[slot] = "unavailable"
    return out


def _capabilities(p: dict) -> dict:
    caps = set(p.get("capabilities", []))
    return {
        "visual_isr": bool(caps & {"visual_ISR", "thermal_ISR"}),
        "radar_scan": "surface_radar" in caps,
        "sonar_scan": bool(caps & {"active_sonar", "passive_sonar"}),
        "relay_comms": "comms_relay" in caps,
    }


# --------------------------------------------------------------------------- #
# Message builders
# --------------------------------------------------------------------------- #
def build_heartbeat(p: dict, dss_id: str, domain: str, comm_mode: str, seq: int) -> dict:
    return {
        "message_type": "heartbeat",
        "vehicle_id": dss_id,
        "timestamp": _iso_now(),
        "sequence": seq,
        "domain": domain,
        "communication_mode": comm_mode,
        "expected_interval_ms": INTERVAL_MS[domain],
    }


def build_telemetry(p: dict, dss_id: str, domain: str) -> dict:
    return {
        "message_type": "telemetry",
        "vehicle_id": dss_id,
        "timestamp": _iso_now(),
        "domain": domain,
        "status": _map_status(p),
        "position": _position(p, domain),
        "velocity": _velocity(p),
        "battery": _battery(p),
        "sensors": _sensors(p),
        "capabilities": _capabilities(p),
        "current_task_id": p.get("current_task"),
    }


def build_link_state(dss_id: str, domain: str, comm_mode: str, status: str,
                     last_contact_ts: float, now: float, with_window: bool) -> dict:
    msg = {
        "message_type": "link_state",
        "vehicle_id": dss_id,
        "timestamp": _iso_now(),
        "domain": domain,
        "communication_mode": comm_mode,
        "status": status,
        "last_contact_at": _iso(last_contact_ts),
        "expected_next_contact_window": None,
    }
    if with_window:
        msg["expected_next_contact_window"] = {
            "start": _iso(now + BLACKOUT_WINDOW_SEC - 300),
            "end": _iso(now + BLACKOUT_WINDOW_SEC),
        }
    return msg


def _severity_for(behavior: str) -> str:
    return {
        "hostile": "critical",
        "suspicious": "high",
        "cooperative": "low",
    }.get((behavior or "").lower(), "medium")


def build_contact_event(contact: dict, reporter_id: str, reporter_domain: str) -> dict:
    behavior = contact.get("behavior")
    return {
        "message_type": "event",
        "event_id": str(contact.get("id")),
        "timestamp": _iso_now(),
        "vehicle_id": reporter_id,
        "domain": reporter_domain,
        "event_kind": "unknown_contact",
        "severity": _severity_for(behavior),
        "position": {"lat": contact.get("lat"), "lon": contact.get("lon"),
                     "alt": None, "depth": None},
        "description": (
            f"{'Dark' if not contact.get('ais') else 'AIS'} contact "
            f"{contact.get('id')} — {behavior or 'unknown'} behaviour, "
            f"{contact.get('speed_knots', '?')} kn"
        ),
        "metadata": {
            "ais": "off" if not contact.get("ais") else "on",
            "speed_knots": contact.get("speed_knots"),
            "heading": contact.get("heading"),
            "behavior": behavior,
        },
    }


def build_alert_event(alert: dict, dss_id: str, domain: str, pos: dict | None) -> dict:
    atype = alert.get("type", "alert")
    severity = {
        "sensor_failure": "high",
        "acoustic_loss": "medium",
        "bingo_warning": "high",
    }.get(atype, "medium")
    position = None
    if pos is not None:
        position = {"lat": pos.get("lat"), "lon": pos.get("lon"),
                    "alt": None, "depth": None}
    return {
        "message_type": "event",
        "event_id": f"alert_{atype}_{alert.get('vehicle')}_{alert.get('sim_time_sec')}",
        "timestamp": _iso_now(),
        "vehicle_id": dss_id,
        "domain": domain,
        "event_kind": atype,
        "severity": severity,
        "position": position,
        "description": alert.get("message", atype),
        "metadata": {"sim_time_sec": alert.get("sim_time_sec")},
    }


# --------------------------------------------------------------------------- #
# Bridge state + per-snapshot processing
# --------------------------------------------------------------------------- #
class BridgeState:
    def __init__(self):
        self.seq = {}              # dss_id -> heartbeat sequence counter
        self.last_hb = {}          # dss_id -> monotonic time of last heartbeat
        self.in_blackout = {}      # dss_id -> bool (last known)
        self.sent_contacts = set() # contact ids already emitted
        self.sent_alerts = set()   # alert event_ids already emitted

    def next_seq(self, dss_id: str) -> int:
        n = self.seq.get(dss_id, 0)
        self.seq[dss_id] = n + 1
        return n


def _is_blackout(p: dict) -> bool:
    return bool(p.get("submerged") or p.get("in_blackout"))


def _nearest_reporter(contact: dict, vehicles: list):
    """Pick the closest non-blackout vehicle to attribute a contact to.

    The DSS event schema requires a reporting vehicle_id; the scenario says a
    USV spotted the threat, so we credit whichever asset is nearest and heard.
    """
    best, best_d = None, float("inf")
    clat, clon = contact.get("lat"), contact.get("lon")
    if clat is None or clon is None:
        return None
    for p in vehicles:
        if p.get("id") not in DSS_ID or _is_blackout(p):
            continue
        d = (p.get("lat", 0) - clat) ** 2 + (p.get("lon", 0) - clon) ** 2
        if d < best_d:
            best, best_d = p, d
    return best


async def _send(dss, msg: dict, label: str):
    await dss.send(json.dumps(msg))


async def process_snapshot(snap: dict, dss, state: BridgeState):
    now = time.time()
    mono = time.monotonic()
    vehicles = snap.get("vehicles", [])

    for p in vehicles:
        sim_id = p.get("id")
        ident = _dss_identity(sim_id)
        if ident is None:
            continue  # AIS vessel / unknown — not a fleet asset
        dss_id, domain, _ = ident
        comm_mode = COMM_MODE.get(p.get("link_type"), "radio")
        last_contact_ts = p.get("last_contact_ts", now)

        blackout = _is_blackout(p)
        was = state.in_blackout.get(dss_id, False)

        if blackout:
            # Honest model: during blackout no telemetry/heartbeat arrives. On
            # entry, tell the DSS to expect silence (subsurface won't degrade).
            if not was:
                await _send(dss, build_link_state(
                    dss_id, domain, comm_mode, "expected_blackout",
                    last_contact_ts, now, with_window=True), "link_state/blackout")
            state.in_blackout[dss_id] = True
            continue

        # Just regained contact -> clear the blackout explicitly.
        if was:
            await _send(dss, build_link_state(
                dss_id, domain, comm_mode, "online",
                last_contact_ts, now, with_window=False), "link_state/online")
        state.in_blackout[dss_id] = False

        # Heartbeat at the domain's expected interval; telemetry every snapshot.
        interval_s = INTERVAL_MS[domain] / 1000.0
        if mono - state.last_hb.get(dss_id, -1e9) >= interval_s:
            await _send(dss, build_heartbeat(
                p, dss_id, domain, comm_mode, state.next_seq(dss_id)), "heartbeat")
            state.last_hb[dss_id] = mono

        await _send(dss, build_telemetry(p, dss_id, domain), "telemetry")

    # Threat contacts -> events (once per id).
    for c in snap.get("contacts", []):
        cid = str(c.get("id"))
        if cid in state.sent_contacts:
            continue
        reporter = _nearest_reporter(c, vehicles)
        if reporter is None:
            r_id, r_domain = "surface_1", "surface"  # sensible default reporter
        else:
            r_id = DSS_ID[reporter["id"]]
            r_domain = DOMAIN_BY_TYPE[_type_of(reporter["id"])]
        await _send(dss, build_contact_event(c, r_id, r_domain), "event/contact")
        state.sent_contacts.add(cid)

    # Notable alerts -> events (once each), so the DSS triage queue sees them.
    by_id = {p.get("id"): p for p in vehicles}
    for a in snap.get("alerts", []):
        sim_vid = a.get("vehicle")
        if sim_vid not in DSS_ID:
            continue  # alerts without a fleet vehicle (e.g. sim_start) are skipped
        dss_id = DSS_ID[sim_vid]
        domain = DOMAIN_BY_TYPE[_type_of(sim_vid)]
        ev = build_alert_event(a, dss_id, domain, by_id.get(sim_vid))
        if ev["event_id"] in state.sent_alerts:
            continue
        await _send(dss, ev, "event/alert")
        state.sent_alerts.add(ev["event_id"])


# --------------------------------------------------------------------------- #
# Connection management (resilient: reconnect both sides on drop)
# --------------------------------------------------------------------------- #
async def _drain_acks(dss):
    """Read and log DSS acknowledgements; surface validation errors loudly."""
    try:
        async for raw in dss:
            try:
                ack = json.loads(raw)
            except Exception:
                continue
            if isinstance(ack, dict) and ack.get("ok") is False:
                print(f"[DSS rejected] {ack.get('error')}: {ack.get('details', '')}")
    except Exception:
        return


async def run_forward():
    """Forward path: sim snapshot stream -> DSS messages (heartbeat/telemetry/...)."""
    state = BridgeState()
    print(f"[bridge] forward: sim={SIM_WS_URL}  ->  dss={DSS_WS_URL}")
    while True:
        try:
            async with websockets.connect(DSS_WS_URL) as dss, \
                       websockets.connect(SIM_WS_URL) as sim:
                print("[bridge] forward connected to both sockets")
                ack_task = asyncio.create_task(_drain_acks(dss))
                try:
                    async for raw in sim:
                        try:
                            snap = json.loads(raw)
                        except Exception:
                            continue
                        await process_snapshot(snap, dss, state)
                finally:
                    ack_task.cancel()
        except Exception as e:
            print(f"[bridge] forward connection lost ({e!r}); retrying in 2s")
            await asyncio.sleep(2)


# --------------------------------------------------------------------------- #
# Reverse path: DSS decision -> sim /command
# --------------------------------------------------------------------------- #
def translate_command(dss_cmd: dict) -> dict | None:
    """Map a DSS command message onto the sim's /command contract.

    Expected DSS shape (README_dss_commands.md):
        {"command_id", "vehicle_id": "sub_1", "action": "...", "params": {...}}
    Returns a sim CommandRequest dict, or None if it can't be translated (logged
    by the caller). Unknown actions/ids are dropped rather than guessed.
    """
    sim_id = SIM_ID.get(dss_cmd.get("vehicle_id"))
    action = DSS_ACTION_MAP.get((dss_cmd.get("action") or "").lower())
    if sim_id is None or action is None:
        return None
    return {
        "command_id": str(dss_cmd.get("command_id") or f"dss_{int(time.time()*1000)}"),
        "vehicle_id": sim_id,
        "action": action,
        "params": dss_cmd.get("params") or {},
        "issued_by": dss_cmd.get("issued_by", "dss"),
        "ts": dss_cmd.get("ts"),
    }


def _post_command(cmd: dict) -> dict:
    """Blocking POST to the sim (run off the event loop via asyncio.to_thread)."""
    r = requests.post(SIM_CMD_URL, json=cmd, timeout=5)
    return r.json()


async def run_reverse():
    """Reverse path: subscribe to the DSS command socket and execute on the sim."""
    print(f"[bridge] reverse: dss_cmd={DSS_CMD_WS_URL}  ->  sim={SIM_CMD_URL}")
    while True:
        try:
            async with websockets.connect(DSS_CMD_WS_URL) as dss_cmd:
                print("[bridge] reverse connected to DSS command socket")
                async for raw in dss_cmd:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    cmd = translate_command(msg)
                    if cmd is None:
                        print(f"[bridge] dropped untranslatable command: {msg}")
                        continue
                    try:
                        ack = await asyncio.to_thread(_post_command, cmd)
                        print(f"[bridge] command -> sim: {cmd['vehicle_id']} "
                              f"{cmd['action']} => {ack.get('status')} ({ack.get('reason')})")
                    except Exception as e:
                        print(f"[bridge] POST /command failed ({e!r})")
        except Exception as e:
            print(f"[bridge] reverse connection lost ({e!r}); retrying in 2s")
            await asyncio.sleep(2)


async def run():
    # Forward and reverse run independently; one socket being down never stops
    # the other (each retries on its own).
    await asyncio.gather(run_forward(), run_reverse())


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[bridge] stopped")

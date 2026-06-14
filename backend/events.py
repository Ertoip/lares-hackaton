"""Event injection timelines for the maritime DSS prototype.

Each scenario is a list of timestamped events that fire relative to the
simulation start. A background loop (driven from main.py) watches the sim
clock and applies each event to the Simulator as its time arrives. Timings
follow §4 of the challenge brief.

Three scenarios are scripted:

  * SCENARIO_A — Underwater contact lost. UUV-1 loses its acoustic link 18 min
    in (turbidity + ambient noise), with ~25 min of blackout before the next
    surfacing window.  Decision classes D4 + D1.
  * SCENARIO_B — Threat appears + vehicle damaged. A fast, dark (no-AIS) contact
    pops up at minute 22; two minutes later USV-2 takes a debris impact and
    loses passive sonar.  Decision classes D3 + D1 + D2.
  * SCENARIO_C — Local GPS jamming. At minute 30 a jamming source degrades GPS
    for UAV-1, UAV-2, and USV-1 (same sector). All three fall back to inertial
    navigation; position uncertainty grows at ~50 m/min. Jamming lifts at
    minute 50.  Decision classes D2 + D1.

Each event is also addressable by `type`, so the UI can fire one manually via
POST /inject/{event_type}.
"""

import math
import time
from perceived_sim import BINGO_PCT
import random

# Scenario A — Underwater contact lost (D4 + D1).
SCENARIO_A = [
    {"t_sec": 0, "type": "sim_start"},
    {"t_sec": 1080, "type": "acoustic_loss", "vehicle": "UUV-1"},   # 18 min
    {"t_sec": 2580, "type": "acoustic_regain", "vehicle": "UUV-1"},  # ~25 min later
]

# Scenario B — Threat appears + vehicle damaged (D3 + D1 + D2).
# lat/lon omitted from threat_contact — apply_event resolves them at fire time
# relative to USV-1's position so the target spawns near the scout drone.
SCENARIO_B = [
    {"t_sec": 0, "type": "sim_start"},
    {
        "t_sec": 1320,  # 22 min
        "type": "threat_contact",
        "contact": {
            "id": "TGT-01",
            "speed_knots": 28, "heading": 210, "ais": False,
            "behavior": "hostile",
        },
    },
    {
        "t_sec": 1440,  # 24 min
        "type": "sensor_failure",
        "vehicle": "USV-2",
        "capability_lost": "passive_sonar",
    },
]

# Scenario C — Local GPS jamming (D2 + D1).
# UAV-1, UAV-2, USV-1 share a sector; jamming drives all three to inertial nav
# with ~50 m/min drift. Jamming lifts at minute 50, GPS re-acquired.
_GPS_JAMMED = ["UAV-1", "UAV-2", "USV-1"]
SCENARIO_C = [
    {"t_sec": 0, "type": "sim_start"},
    *[
        {"t_sec": 1800, "type": "gps_jamming_start", "vehicle": vid}
        for vid in _GPS_JAMMED
    ],
    *[
        {"t_sec": 3000, "type": "gps_jamming_end", "vehicle": vid}
        for vid in _GPS_JAMMED
    ],
]

# Selectable by name from main.py / a REST endpoint.
SCENARIOS = {
    "A": SCENARIO_A,
    "B": SCENARIO_B,
    "C": SCENARIO_C,
}


def apply_event(sim, event: dict):
    """Mutate simulator state according to a single event.

    Returns a human-readable description of what happened, or None if the
    event type was unknown.
    """
    etype = event.get("type")

    if etype == "sim_start":
        desc = "Simulation started"

    elif etype == "acoustic_loss":
        vid = event["vehicle"]
        sim.submerge(vid)
        desc = f"{vid} acoustic link lost — entering blackout (dead-reckoning)"

    elif etype == "acoustic_regain":
        vid = event["vehicle"]
        sim.surface(vid)
        desc = f"{vid} surfaced — acoustic link re-established"

    elif etype == "threat_contact":
        contact = dict(event["contact"])
        if "lat" not in contact or "lon" not in contact:
            from geo import snap_to_water
            anchor = sim.perceived.get("USV-1") or {}
            base_lat = anchor.get("lat", 51.10)
            base_lon = anchor.get("lon", 1.55)
            # random bearing and radius 3–8 km (≈0.027–0.072° at 51°N)
            angle_rad = random.uniform(0, 2 * math.pi)
            radius_deg = random.uniform(0.027, 0.072)
            raw_lat = base_lat + radius_deg * math.cos(angle_rad)
            raw_lon = base_lon + radius_deg * math.sin(angle_rad)
            contact["lat"], contact["lon"] = snap_to_water(raw_lat, raw_lon)
        sim.add_contact(contact)
        desc = f"Threat contact {contact.get('id')} detected ({contact.get('behavior')})"

    elif etype == "sensor_failure":
        vid = event["vehicle"]
        cap = event.get("capability_lost")
        sim.remove_capability(vid, cap)
        desc = f"{vid} sensor failure — lost {cap}"

    elif etype == "gps_jamming_start":
        vid = event["vehicle"]
        sim.start_gps_jamming(vid)
        desc = f"{vid} GPS denied — falling back to inertial navigation (~50 m/min drift)"

    elif etype == "gps_jamming_end":
        vid = event["vehicle"]
        sim.stop_gps_jamming(vid)
        desc = f"{vid} GPS re-acquired — position uncertainty reset"

    elif etype == "bingo_warning":
        vid = event["vehicle"]
        _p = sim.perceived.get(vid)
        _default_pct = BINGO_PCT.get(_p["type"], 20) if _p else 20
        pct = event.get("battery_pct", _default_pct)
        # Same entry point as organic bingo: sets battery+status AND starts the
        # auto-return. push_alert=False because apply_event emits its own below.
        sim.trigger_bingo(vid, battery_pct=pct, push_alert=False)
        desc = f"{vid} BINGO fuel — battery at {pct}%"

    else:
        return None

    sim.push_alert({
        "type": etype,
        "vehicle": event.get("vehicle"),
        "message": desc,
    })
    return desc


class EventScheduler:
    """Tracks which scheduled events have fired and applies due ones."""

    def __init__(self, sim, scenario=None):
        # `scenario` may be a name ("A"/"B"), an explicit list, or None (-> A).
        if isinstance(scenario, str):
            scenario = SCENARIOS.get(scenario, SCENARIO_A)
        self.sim = sim
        self.scenario = sorted(scenario or SCENARIO_A, key=lambda e: e["t_sec"])
        self._fired = set()  # indices already applied

    def load_scenario(self, name: str) -> bool:
        """Switch to a named scenario and reset fired state. Returns success."""
        scenario = SCENARIOS.get(name)
        if scenario is None:
            return False
        self.scenario = sorted(scenario, key=lambda e: e["t_sec"])
        self._fired = set()
        return True

    def step(self):
        """Apply any scheduled events whose time has arrived."""
        now = self.sim.sim_time_sec
        for i, event in enumerate(self.scenario):
            if i in self._fired:
                continue
            if now >= event["t_sec"]:
                apply_event(self.sim, event)
                self._fired.add(i)

    def next_event_time(self):
        """Sim-time (sec) of the earliest event not yet fired, or None."""
        pending = [
            e["t_sec"] for i, e in enumerate(self.scenario) if i not in self._fired
        ]
        return min(pending) if pending else None

    def fire_by_type(self, event_type: str):
        """Manually fire the first scenario event matching a type.

        If the type isn't in the active scenario, a sensible default event is
        synthesised so demo buttons always do something.
        """
        for event in self.scenario:
            if event["type"] == event_type:
                return apply_event(self.sim, event)

        _bingo_vid = "UAV-2"
        _bingo_p = self.sim.perceived.get(_bingo_vid)
        _bingo_pct = BINGO_PCT.get(_bingo_p["type"], 20) if _bingo_p else 20
        defaults = {
            "acoustic_loss": {"type": "acoustic_loss", "vehicle": "UUV-2"},
            "acoustic_regain": {"type": "acoustic_regain", "vehicle": "UUV-2"},
            "threat_contact": {
                "type": "threat_contact",
                "contact": {
                    "id": f"TGT-{int(time.time()) % 100:02d}",
                    "lat": 51.03, "lon": 1.58, "speed_knots": 24,
                    "heading": 180, "ais": False, "behavior": "hostile",
                },
            },
            "sensor_failure": {
                "type": "sensor_failure", "vehicle": "USV-1",
                "capability_lost": "active_sonar",
            },
            "bingo_warning": {"type": "bingo_warning", "vehicle": _bingo_vid, "battery_pct": _bingo_pct},
            "gps_jamming_start": {"type": "gps_jamming_start", "vehicle": "UAV-1"},
            "gps_jamming_end": {"type": "gps_jamming_end", "vehicle": "UAV-1"},
        }
        event = defaults.get(event_type)
        if event:
            return apply_event(self.sim, event)
        return None

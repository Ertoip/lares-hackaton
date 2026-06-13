"""Event injection timelines for the maritime DSS prototype.

Each scenario is a list of timestamped events that fire relative to the
simulation start. A background loop (driven from main.py) watches the sim
clock and applies each event to the Simulator as its time arrives. Timings
follow §4 of the challenge brief.

Two scenarios are scripted:

  * SCENARIO_A — Underwater contact lost. UUV-1 loses its acoustic link 18 min
    in (turbidity + ambient noise), with ~25 min of blackout before the next
    surfacing window.  Decision classes D4 + D1.
  * SCENARIO_B — Threat appears + vehicle damaged. A fast, dark (no-AIS) contact
    pops up at minute 22; two minutes later USV-2 takes a debris impact and
    loses passive sonar.  Decision classes D3 + D1 + D2.

Each event is also addressable by `type`, so the UI can fire one manually via
POST /inject/{event_type}.
"""

import time

# Scenario A — Underwater contact lost (D4 + D1).
SCENARIO_A = [
    {"t_sec": 0, "type": "sim_start"},
    {"t_sec": 1080, "type": "acoustic_loss", "vehicle": "UUV-1"},   # 18 min
    {"t_sec": 2580, "type": "acoustic_regain", "vehicle": "UUV-1"},  # ~25 min later
]

# Scenario B — Threat appears + vehicle damaged (D3 + D1 + D2).
SCENARIO_B = [
    {"t_sec": 0, "type": "sim_start"},
    {
        "t_sec": 1320,  # 22 min
        "type": "threat_contact",
        "contact": {
            "id": "TGT-01", "lat": 51.06, "lon": 1.65,
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

# Selectable by name from main.py / a REST endpoint.
SCENARIOS = {
    "A": SCENARIO_A,
    "B": SCENARIO_B,
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
        contact = event["contact"]
        sim.add_contact(contact)
        desc = f"Threat contact {contact.get('id')} detected ({contact.get('behavior')})"

    elif etype == "sensor_failure":
        vid = event["vehicle"]
        cap = event.get("capability_lost")
        sim.remove_capability(vid, cap)
        desc = f"{vid} sensor failure — lost {cap}"

    elif etype == "bingo_warning":
        vid = event["vehicle"]
        pct = event.get("battery_pct", 20)
        sim.set_battery(vid, pct)
        sim.set_status(vid, "bingo")
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

    def fire_by_type(self, event_type: str):
        """Manually fire the first scenario event matching a type.

        If the type isn't in the active scenario, a sensible default event is
        synthesised so demo buttons always do something.
        """
        for event in self.scenario:
            if event["type"] == event_type:
                return apply_event(self.sim, event)

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
            "bingo_warning": {"type": "bingo_warning", "vehicle": "UAV-2", "battery_pct": 18},
        }
        event = defaults.get(event_type)
        if event:
            return apply_event(self.sim, event)
        return None

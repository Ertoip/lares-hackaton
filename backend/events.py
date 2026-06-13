"""Event injection timeline for the maritime DSS prototype.

SCENARIO_B is a list of timestamped events that fire relative to the
simulation start. A background loop (driven from main.py) watches the
sim clock and applies each event to the Simulator as its time arrives.

Each event is also addressable by `type`, so the UI can fire one
manually through POST /inject/{event_type}.
"""

import time

SCENARIO_B = [
    {"t_sec": 0, "type": "sim_start"},
    {"t_sec": 1080, "type": "acoustic_loss", "vehicle": "UUV-1"},
    {
        "t_sec": 1320,
        "type": "threat_contact",
        "contact": {
            "id": "TGT-01", "lat": 51.06, "lon": 1.65,
            "speed_knots": 28, "heading": 210, "ais": False,
            "behavior": "hostile",
        },
    },
    {
        "t_sec": 1440,
        "type": "sensor_failure",
        "vehicle": "USV-2",
        "capability_lost": "passive_sonar",
    },
    {"t_sec": 1500, "type": "bingo_warning", "vehicle": "UAV-1", "battery_pct": 22},
]


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
        self.sim = sim
        self.scenario = sorted(scenario or SCENARIO_B, key=lambda e: e["t_sec"])
        self._fired = set()  # indices already applied

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

        If the type isn't in the scenario, a sensible default event is
        synthesised so demo buttons always do something.
        """
        for event in self.scenario:
            if event["type"] == event_type:
                return apply_event(self.sim, event)

        defaults = {
            "acoustic_loss": {"type": "acoustic_loss", "vehicle": "UUV-2"},
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

"""Synthetic telemetry generator for the maritime DSS prototype.

Simulates 6 unmanned vehicles (2 UAV, 2 USV, 2 UUV) operating in the
Strait of Messina. Each vehicle moves along its heading each tick with a
little Gaussian position noise and a slow battery drain.

UUVs, when submerged, stop emitting position updates. Their position is
dead-reckoned internally from the last known fix with a growing
uncertainty radius (20 m at dive, +10 m per minute of blackout).
"""

import math
import random
import time

from geo import is_water, snap_to_water

# Vehicle types that must stay on the water (surface + subsurface).
WATER_TYPES = {"USV", "UUV"}

# Update cadence per vehicle type (seconds between position updates).
UPDATE_RATES = {
    "UAV": 0.5,
    "USV": 1.0,
    "UUV": 5.0,
}

# Default comms link per vehicle type.
LINK_TYPES = {
    "UAV": "radio",
    "USV": "satellite",
    "UUV": "acoustic",
}

# One nautical mile expressed in degrees of latitude.
NM_PER_DEG_LAT = 60.0


def _knots_to_deg_per_sec_lat(speed_knots: float) -> float:
    """Convert speed in knots to degrees of latitude travelled per second."""
    nm_per_sec = speed_knots / 3600.0
    return nm_per_sec / NM_PER_DEG_LAT


# Initial fleet definition. All vehicles seeded inside the Strait of Messina
# box (38.1-38.3 N, 15.3-15.7 E).
VEHICLE_DEFS = [
    {
        "id": "UAV-1", "type": "UAV", "lat": 38.265, "lon": 15.41,
        "heading": 120.0, "speed_knots": 60.0, "battery_pct": 78.0,
        "capabilities": ["visual_ISR", "thermal_ISR"],
        "current_task": "Wide-area ISR patrol",
    },
    {
        "id": "UAV-2", "type": "UAV", "lat": 38.13, "lon": 15.62,
        "heading": 300.0, "speed_knots": 55.0, "battery_pct": 91.0,
        "capabilities": ["visual_ISR", "comms_relay"],
        "current_task": "Comms relay orbit",
    },
    {
        "id": "USV-1", "type": "USV", "lat": 38.21, "lon": 15.48,
        "heading": 45.0, "speed_knots": 18.0, "battery_pct": 84.0,
        "capabilities": ["surface_radar", "active_sonar"],
        "current_task": "Picket line patrol",
    },
    {
        "id": "USV-2", "type": "USV", "lat": 38.18, "lon": 15.58,
        "heading": 210.0, "speed_knots": 14.0, "battery_pct": 88.0,
        "capabilities": ["surface_radar", "passive_sonar"],
        "current_task": "ASW screen",
    },
    {
        "id": "UUV-1", "type": "UUV", "lat": 38.20, "lon": 15.52,
        "heading": 90.0, "speed_knots": 4.0, "battery_pct": 73.0,
        "capabilities": ["passive_sonar", "seabed_mapping"],
        "current_task": "Subsurface survey",
    },
    {
        "id": "UUV-2", "type": "UUV", "lat": 38.24, "lon": 15.50,
        "heading": 270.0, "speed_knots": 3.5, "battery_pct": 66.0,
        "capabilities": ["passive_sonar", "mine_countermeasures"],
        "current_task": "MCM sweep",
    },
]


class Simulator:
    """Holds and advances the state of the whole fleet."""

    def __init__(self):
        self.start_time = time.time()
        self.vehicles = {}
        self.contacts = []   # threat contacts
        self.alerts = []     # recent alert records broadcast to the UI
        # Per-vehicle bookkeeping that is not part of the public state.
        self._last_update = {}
        self._dive_info = {}  # UUV id -> {lat, lon, heading, speed, dive_ts}
        # Live data providers, attached by main.py (may stay None).
        self.weather = None
        self.ais = None

        now = time.time()
        for d in VEHICLE_DEFS:
            v = dict(d)
            # Water vehicles seeded on land are nudged to the nearest sea cell.
            if v["type"] in WATER_TYPES and not is_water(v["lat"], v["lon"]):
                v["lat"], v["lon"] = snap_to_water(v["lat"], v["lon"])
            v["link_type"] = LINK_TYPES[v["type"]]
            v["link_quality"] = round(random.uniform(0.85, 1.0), 2)
            v["last_contact_ts"] = now
            v["status"] = "nominal"
            v["submerged"] = False
            v["waypoint"] = None  # {"lat":..., "lon":...} when assigned
            self.vehicles[v["id"]] = v
            self._last_update[v["id"]] = now

    # ------------------------------------------------------------------ #
    # Time helpers
    # ------------------------------------------------------------------ #
    @property
    def sim_time_sec(self) -> int:
        return int(time.time() - self.start_time)

    # ------------------------------------------------------------------ #
    # Core tick
    # ------------------------------------------------------------------ #
    def tick(self):
        """Advance every vehicle that is due for an update."""
        now = time.time()
        for vid, v in self.vehicles.items():
            rate = UPDATE_RATES[v["type"]]
            elapsed = now - self._last_update[vid]
            if elapsed < rate:
                continue

            # Submerged UUVs go silent: no public position update, no
            # contact timestamp refresh. Their estimate is dead-reckoned
            # on demand via estimate_position().
            if v.get("submerged"):
                self._last_update[vid] = now
                continue

            self._move(v, elapsed)
            self._drain_battery(v, elapsed)
            v["last_contact_ts"] = now
            self._last_update[vid] = now

            # Auto-mark bingo if battery is critically low.
            if v["battery_pct"] <= 15 and v["status"] == "nominal":
                v["status"] = "bingo"

    def _move(self, v, elapsed):
        # Steer gently toward an assigned waypoint, if any.
        if v.get("waypoint"):
            wp = v["waypoint"]
            v["heading"] = self._bearing(v["lat"], v["lon"], wp["lat"], wp["lon"])
            if self._haversine_m(v["lat"], v["lon"], wp["lat"], wp["lon"]) < 150:
                v["waypoint"] = None  # arrived

        step_lat = _knots_to_deg_per_sec_lat(v["speed_knots"]) * elapsed
        hdg = math.radians(v["heading"])
        dlat = step_lat * math.cos(hdg)
        coslat = max(0.1, math.cos(math.radians(v["lat"])))
        dlon = (step_lat * math.sin(hdg)) / coslat

        # Small Gaussian position noise.
        dlat += random.gauss(0, 0.00003)
        dlon += random.gauss(0, 0.00003)

        new_lat = round(v["lat"] + dlat, 6)
        new_lon = round(v["lon"] + dlon, 6)

        # Water vehicles must not cross onto land: if the next step would beach
        # them, hold position and turn back toward open water instead.
        if v["type"] in WATER_TYPES and not is_water(new_lat, new_lon):
            v["heading"] = (v["heading"] + 180 + random.uniform(-40, 40)) % 360
            return

        v["lat"] = new_lat
        v["lon"] = new_lon

        # Keep vehicles loosely inside the operating box by bouncing.
        if not (38.05 <= v["lat"] <= 38.35):
            v["heading"] = (v["heading"] + 180) % 360
            v["lat"] = min(38.35, max(38.05, v["lat"]))
        if not (15.25 <= v["lon"] <= 15.75):
            v["heading"] = (v["heading"] + 180) % 360
            v["lon"] = min(15.75, max(15.25, v["lon"]))

        # Tiny heading drift so tracks aren't perfectly straight.
        v["heading"] = (v["heading"] + random.gauss(0, 1.5)) % 360

    def _drain_battery(self, v, elapsed):
        # Aerial vehicles burn faster than surface/subsurface.
        rate = 0.02 if v["type"] == "UAV" else 0.008
        v["battery_pct"] = round(max(0.0, v["battery_pct"] - rate * elapsed), 1)

    # ------------------------------------------------------------------ #
    # Dead reckoning for submerged UUVs
    # ------------------------------------------------------------------ #
    def submerge(self, vid: str):
        v = self.vehicles.get(vid)
        if not v:
            return
        v["submerged"] = True
        v["status"] = "lost_comms" if v["status"] == "nominal" else v["status"]
        self._dive_info[vid] = {
            "lat": v["lat"], "lon": v["lon"],
            "heading": v["heading"], "speed_knots": v["speed_knots"],
            "dive_ts": time.time(),
        }

    def surface(self, vid: str):
        v = self.vehicles.get(vid)
        if not v:
            return
        est = self.estimate_position(vid)
        if est:
            v["lat"], v["lon"] = est["lat"], est["lon"]
        v["submerged"] = False
        v["status"] = "nominal"
        v["last_contact_ts"] = time.time()
        self._dive_info.pop(vid, None)

    def estimate_position(self, vid: str):
        """Return dead-reckoned position + uncertainty for a submerged UUV."""
        v = self.vehicles.get(vid)
        if not v or not v.get("submerged"):
            return None
        info = self._dive_info.get(vid)
        if not info:
            return None

        blackout = time.time() - info["dive_ts"]
        step_lat = _knots_to_deg_per_sec_lat(info["speed_knots"]) * blackout
        hdg = math.radians(info["heading"])
        dlat = step_lat * math.cos(hdg)
        coslat = max(0.1, math.cos(math.radians(info["lat"])))
        dlon = (step_lat * math.sin(hdg)) / coslat

        uncertainty = 20.0 + 10.0 * (blackout / 60.0)
        return {
            "vehicle_id": vid,
            "lat": round(info["lat"] + dlat, 6),
            "lon": round(info["lon"] + dlon, 6),
            "uncertainty_radius_m": round(uncertainty, 1),
            "blackout_duration_sec": round(blackout, 1),
        }

    # ------------------------------------------------------------------ #
    # Mutators used by the event system / REST API
    # ------------------------------------------------------------------ #
    def add_contact(self, contact: dict):
        # Avoid duplicate ids.
        self.contacts = [c for c in self.contacts if c.get("id") != contact.get("id")]
        contact = dict(contact)
        contact["detected_ts"] = time.time()
        self.contacts.append(contact)

    def remove_capability(self, vid: str, capability: str):
        v = self.vehicles.get(vid)
        if v and capability in v.get("capabilities", []):
            v["capabilities"].remove(capability)
            if v["status"] == "nominal":
                v["status"] = "degraded"

    def set_status(self, vid: str, status: str):
        v = self.vehicles.get(vid)
        if v:
            v["status"] = status

    def set_battery(self, vid: str, pct: float):
        v = self.vehicles.get(vid)
        if v:
            v["battery_pct"] = pct

    def assign_task(self, vid: str, task: str, lat=None, lon=None):
        v = self.vehicles.get(vid)
        if not v:
            return None
        v["current_task"] = task
        if lat is not None and lon is not None:
            # A water vehicle can't reach a waypoint on land — snap it to sea.
            if v["type"] in WATER_TYPES and not is_water(lat, lon):
                lat, lon = snap_to_water(lat, lon)
            v["waypoint"] = {"lat": lat, "lon": lon}
        return v

    def push_alert(self, alert: dict):
        alert = dict(alert)
        alert.setdefault("ts", time.time())
        alert.setdefault("sim_time_sec", self.sim_time_sec)
        self.alerts.append(alert)
        # Keep only the most recent handful for broadcast.
        self.alerts = self.alerts[-10:]

    # ------------------------------------------------------------------ #
    # Snapshots
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        return {
            "vehicles": list(self.vehicles.values()),
            "contacts": self.contacts,
            "alerts": self.alerts,
            "sim_time_sec": self.sim_time_sec,
            "weather": self.weather.current if self.weather else None,
            "ais": self.ais.vessels() if self.ais else [],
        }

    # ------------------------------------------------------------------ #
    # Geometry helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bearing(lat1, lon1, lat2, lon2):
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        y = math.sin(dlon) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    @staticmethod
    def _haversine_m(lat1, lon1, lat2, lon2):
        r = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

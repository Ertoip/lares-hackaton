"""Perceived-only telemetry simulator for the maritime DSS prototype.

This is the single-state alternative to ``simulator.py``. Instead of keeping a
hidden *ground truth* and reconstructing a *perceived* picture from dropped /
delayed / noised telemetry packets, this module simulates the **operator's
picture directly**:

  * every vehicle has ONE estimated position that moves along its track,
  * a comms-availability roll each cycle decides whether a fresh fix arrives,
  * when no fix arrives, the estimate **dead-reckons** forward from the last
    fix and its uncertainty ``sigma_m`` grows; when a fix arrives, the fix
    advances and ``sigma_m`` resets to the link's baseline.

This is the picture a DSS solver and the UI actually consume — neither is ever
given a "real" position, which is exactly right for decision support under
uncertainty. A submerged UUV is just the extreme case: its acoustic link goes
to near-total blackout, so fixes stop arriving and ``sigma_m`` balloons until
it surfaces.

Public surface (kept identical to ``simulator.Simulator`` so it is a drop-in
replacement for main.py and events.py):

    tick(), snapshot(), estimate_position(vid), assign_task(...),
    submerge(vid), surface(vid), add_contact(c), remove_capability(...),
    set_status(...), set_battery(...), push_alert(...),
    .perceived, .contacts, .alerts, .sim_time_sec, .weather, .ais

Fleet: 6 unmanned vehicles (2 UAV, 2 USV, 2 UUV) in the Dover Strait.
"""

import math
import random
import time

from geo import is_water, snap_to_water
from comms import (
    drop_probability,
    base_sigma_m,
    sigma_growth_m_per_min,
    residual_bandwidth_kbps,
)

# Vehicle types that must stay on the water (surface + subsurface).
WATER_TYPES = {"USV", "UUV"}

# Cadence per vehicle type: seconds between fix *attempts* (a telemetry cycle).
UPDATE_RATES = {"UAV": 0.5, "USV": 1.0, "UUV": 5.0}

# Default comms link per vehicle type.
LINK_TYPES = {"UAV": "radio", "USV": "satellite", "UUV": "acoustic"}

# A fix older than (factor x cycle) is flagged stale / in blackout.
STALE_FACTOR = 4

# Operating depth (z, metres) by type. UAVs fly, USVs sit at the surface,
# UUVs cruise submerged; `submerge()` takes a UUV deeper still.
DEFAULT_Z = {"UAV": 150.0, "USV": 0.0, "UUV": -40.0}
SUBMERGED_Z = -80.0

PAYLOAD_DEFAULTS = {"UAV": "sensors_active", "USV": "sensors_active", "UUV": "survey_active"}

# One nautical mile expressed in degrees of latitude.
NM_PER_DEG_LAT = 60.0
KN_TO_MPS = 0.514444

# Cone (anisotropic) uncertainty model. While a vehicle is unheard-from, its
# position uncertainty is an ellipse aligned with the last known heading:
#   * along-track grows with TIME (we don't know if it held speed/coasted/stopped),
#   * cross-track grows with DISTANCE x accumulated heading drift (it fans out).
# Together they sweep the cone the operator must search. Tune with the mentor.
DRIFT_DEG_PER_MIN = 6.0     # how fast the heading-drift half-angle accumulates
MAX_DRIFT_DEG = 45.0        # ceiling on the heading-drift half-angle

# Operating-area box (Dover Strait).
LAT_MIN, LAT_MAX = 50.85, 51.25
LON_MIN, LON_MAX = 0.90, 2.20


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _knots_to_deg_per_sec_lat(speed_knots: float) -> float:
    """Convert speed in knots to degrees of latitude travelled per second."""
    return (speed_knots / 3600.0) / NM_PER_DEG_LAT


# Initial fleet definition. All vehicles seeded inside the Dover Strait box.
VEHICLE_DEFS = [
    {
        "id": "UAV-1", "type": "UAV", "lat": 51.12, "lon": 1.30,
        "heading": 120.0, "speed_knots": 60.0, "battery_pct": 78.0,
        "capabilities": ["visual_ISR", "thermal_ISR"],
        "current_task": "Wide-area ISR patrol",
    },
    {
        "id": "UAV-2", "type": "UAV", "lat": 50.95, "lon": 1.98,
        "heading": 300.0, "speed_knots": 55.0, "battery_pct": 91.0,
        "capabilities": ["visual_ISR", "comms_relay"],
        "current_task": "Comms relay orbit",
    },
    {
        "id": "USV-1", "type": "USV", "lat": 51.05, "lon": 1.45,
        "heading": 45.0, "speed_knots": 18.0, "battery_pct": 84.0,
        "capabilities": ["surface_radar", "active_sonar"],
        "current_task": "Picket line patrol",
    },
    {
        "id": "USV-2", "type": "USV", "lat": 51.02, "lon": 1.75,
        "heading": 210.0, "speed_knots": 14.0, "battery_pct": 88.0,
        "capabilities": ["surface_radar", "passive_sonar"],
        "current_task": "ASW screen",
    },
    {
        "id": "UUV-1", "type": "UUV", "lat": 51.08, "lon": 1.60,
        "heading": 90.0, "speed_knots": 4.0, "battery_pct": 73.0,
        "capabilities": ["passive_sonar", "seabed_mapping"],
        "current_task": "Subsurface survey",
    },
    {
        "id": "UUV-2", "type": "UUV", "lat": 51.00, "lon": 1.55,
        "heading": 270.0, "speed_knots": 3.5, "battery_pct": 66.0,
        "capabilities": ["passive_sonar", "mine_countermeasures"],
        "current_task": "MCM sweep",
    },
]


class PerceivedSimulator:
    """Advances the operator's perceived picture directly (single state)."""

    def __init__(self):
        self.start_time = time.time()
        self.perceived = {}       # vid -> estimate record (the only state)
        self.contacts = []        # threat contacts
        self.alerts = []          # recent alert records broadcast to the UI
        self._last_attempt = {}   # vid -> last fix-attempt time
        # Live data providers, attached by main.py (may stay None).
        self.weather = None
        self.ais = None

        now = time.time()
        for d in VEHICLE_DEFS:
            self._seed(dict(d), now)

    # ------------------------------------------------------------------ #
    # Seeding
    # ------------------------------------------------------------------ #
    def _seed(self, d, now):
        vid, vtype = d["id"], d["type"]
        lat, lon = d["lat"], d["lon"]
        if vtype in WATER_TYPES and not is_water(lat, lon):
            lat, lon = snap_to_water(lat, lon)

        p = {
            "id": vid,
            "type": vtype,
            "link_type": LINK_TYPES[vtype],
            # --- last received fix (the anchor we dead-reckon from) ---
            "fix_lat": lat, "fix_lon": lon, "fix_z_m": DEFAULT_Z[vtype],
            "fix_heading": d["heading"], "fix_speed_knots": d["speed_knots"],
            "last_contact_ts": now,
            # --- evolving scalar state (known at last contact) ---
            "z_m": DEFAULT_Z[vtype],
            "battery_pct": d["battery_pct"],
            "status": "nominal",
            "submerged": False,
            "capabilities": list(d["capabilities"]),
            "sensor_health": {cap: 1.0 for cap in d["capabilities"]},
            "payload_state": PAYLOAD_DEFAULTS[vtype],
            "link_quality": round(random.uniform(0.85, 1.0), 2),
            "current_task": d["current_task"],
            "waypoint": None,
        }
        self.perceived[vid] = p
        self._last_attempt[vid] = now
        self._update_estimate(p, now)

    # ------------------------------------------------------------------ #
    # Time / environment helpers
    # ------------------------------------------------------------------ #
    @property
    def sim_time_sec(self) -> int:
        return int(time.time() - self.start_time)

    def _sea_state(self):
        """Current Douglas sea state from live weather, or None (-> default)."""
        if self.weather and self.weather.current:
            cur = self.weather.current
            if "error" not in cur:
                return cur.get("sea_state")
        return None

    # ------------------------------------------------------------------ #
    # Core tick
    # ------------------------------------------------------------------ #
    def tick(self):
        """Run one telemetry cycle per due vehicle, then refresh all estimates."""
        now = time.time()
        sea = self._sea_state()

        for vid, p in self.perceived.items():
            if now - self._last_attempt[vid] >= UPDATE_RATES[p["type"]]:
                self._attempt_contact(p, now, sea)
                self._last_attempt[vid] = now
            # Every tick: dead-reckon the estimate forward and grow sigma.
            self._update_estimate(p, now)

    def _attempt_contact(self, p, now, sea):
        """One telemetry cycle: maybe receive a fresh fix; always age scalars."""
        # Battery + link quality evolve whether or not the packet gets through.
        self._drain_battery(p, now)
        self._update_link_quality(p, sea)
        if p["battery_pct"] <= 15 and p["status"] == "nominal":
            p["status"] = "bingo"

        # Does a fresh fix arrive this cycle? (submerged acoustic ~ never)
        sea_val = sea if sea is not None else 3
        p_drop = drop_probability(
            p["link_type"], sea_val, p.get("z_m", 0.0), p.get("submerged", False)
        )
        if random.random() >= p_drop:
            self._advance_fix(p, now)  # contact received -> new anchor, sigma resets

    def _advance_fix(self, p, now):
        """Move the fix anchor to where the vehicle now is and re-aim it.

        With no ground truth, the "real" motion lives here: the anchor steps
        forward along its heading by the time since the last fix (plus small
        wander), then steers toward any waypoint and drifts a little.
        """
        dt = max(0.0, now - p["last_contact_ts"])

        step_lat = _knots_to_deg_per_sec_lat(p["fix_speed_knots"]) * dt
        hdg = math.radians(p["fix_heading"])
        dlat = step_lat * math.cos(hdg) + random.gauss(0, 0.00003)
        coslat = max(0.1, math.cos(math.radians(p["fix_lat"])))
        dlon = (step_lat * math.sin(hdg)) / coslat + random.gauss(0, 0.00003)

        new_lat = round(p["fix_lat"] + dlat, 6)
        new_lon = round(p["fix_lon"] + dlon, 6)

        # Water vehicles must not cross onto land: bounce back toward open sea.
        if p["type"] in WATER_TYPES and not is_water(new_lat, new_lon):
            p["fix_heading"] = (p["fix_heading"] + 180 + random.uniform(-40, 40)) % 360
            p["last_contact_ts"] = now
            return

        p["fix_lat"], p["fix_lon"] = new_lat, new_lon
        p["fix_z_m"] = p["z_m"]
        p["last_contact_ts"] = now

        # Steer toward an assigned waypoint, if any.
        wp = p.get("waypoint")
        if wp:
            p["fix_heading"] = self._bearing(new_lat, new_lon, wp["lat"], wp["lon"])
            if self._haversine_m(new_lat, new_lon, wp["lat"], wp["lon"]) < 150:
                p["waypoint"] = None  # arrived

        # Keep inside the operating box by bouncing, plus tiny heading drift.
        if not (LAT_MIN <= p["fix_lat"] <= LAT_MAX):
            p["fix_heading"] = (p["fix_heading"] + 180) % 360
            p["fix_lat"] = _clamp(p["fix_lat"], LAT_MIN, LAT_MAX)
        if not (LON_MIN <= p["fix_lon"] <= LON_MAX):
            p["fix_heading"] = (p["fix_heading"] + 180) % 360
            p["fix_lon"] = _clamp(p["fix_lon"], LON_MIN, LON_MAX)
        p["fix_heading"] = (p["fix_heading"] + random.gauss(0, 1.5)) % 360

    def _drain_battery(self, p, now):
        elapsed = now - self._last_attempt[p["id"]]
        rate = 0.02 if p["type"] == "UAV" else 0.008
        p["battery_pct"] = round(max(0.0, p["battery_pct"] - rate * elapsed), 1)

    def _update_link_quality(self, p, sea):
        """Bounded random walk toward the quality implied by conditions."""
        implied = 1.0 - drop_probability(
            p["link_type"], sea if sea is not None else 3,
            p.get("z_m", 0.0), p.get("submerged", False),
        )
        q = p["link_quality"] + (implied - p["link_quality"]) * 0.2 + random.gauss(0, 0.02)
        p["link_quality"] = round(_clamp(q, 0.0, 1.0), 2)

    # ------------------------------------------------------------------ #
    # Estimate refresh (dead-reckon + grow uncertainty)
    # ------------------------------------------------------------------ #
    def _update_estimate(self, p, now):
        """Project the displayed estimate forward from the fix and size sigma."""
        age = max(0.0, now - p["last_contact_ts"])

        step_lat = _knots_to_deg_per_sec_lat(p["fix_speed_knots"]) * age
        hdg = math.radians(p["fix_heading"])
        dlat = step_lat * math.cos(hdg)
        coslat = max(0.1, math.cos(math.radians(p["fix_lat"])))
        dlon = (step_lat * math.sin(hdg)) / coslat

        p["lat"] = round(p["fix_lat"] + dlat, 6)
        p["lon"] = round(p["fix_lon"] + dlon, 6)
        p["heading"] = round(p["fix_heading"], 1)
        p["speed_knots"] = p["fix_speed_knots"]
        p["velocity"] = {
            "vx_kn": round(p["fix_speed_knots"] * math.sin(hdg), 3),   # east
            "vy_kn": round(p["fix_speed_knots"] * math.cos(hdg), 3),   # north
        }

        link = p["link_type"]
        base = base_sigma_m(link)
        age_min = age / 60.0
        # Along-track (forward/back): speed/timing uncertainty, grows with time.
        sigma_along = base + sigma_growth_m_per_min(link) * age_min
        # Cross-track (sideways): heading drift over the distance travelled.
        dist_m = p["fix_speed_knots"] * KN_TO_MPS * age
        drift = math.radians(min(MAX_DRIFT_DEG, DRIFT_DEG_PER_MIN * age_min))
        sigma_cross = base + dist_m * math.tan(drift)
        p["sigma_along_m"] = round(sigma_along, 1)
        p["sigma_cross_m"] = round(sigma_cross, 1)
        p["uncertainty_heading_deg"] = p["heading"]  # ellipse major axis = heading
        # Backward-compatible scalar radius: bounding circle of the ellipse.
        p["sigma_m"] = round(max(sigma_along, sigma_cross), 1)
        p["age_sec"] = round(age, 1)
        p["stale"] = age > STALE_FACTOR * UPDATE_RATES[p["type"]]
        p["in_blackout"] = p["stale"]
        p["residual_bandwidth_kbps"] = residual_bandwidth_kbps(link, p.get("link_quality") or 0.0)
        if p.get("submerged"):
            p["expected_next_contact_sec"] = None
        else:
            p["expected_next_contact_sec"] = round(max(0.0, UPDATE_RATES[p["type"]] - age), 1)

    # ------------------------------------------------------------------ #
    # Submerge / surface (acoustic blackout is just an extreme of comms)
    # ------------------------------------------------------------------ #
    def submerge(self, vid: str):
        p = self.perceived.get(vid)
        if not p:
            return
        p["submerged"] = True
        p["z_m"] = SUBMERGED_Z
        if p["status"] == "nominal":
            p["status"] = "lost_comms"

    def surface(self, vid: str):
        p = self.perceived.get(vid)
        if not p:
            return
        p["submerged"] = False
        p["z_m"] = DEFAULT_Z[p["type"]]
        p["status"] = "nominal"
        p["last_contact_ts"] = time.time()  # re-established contact -> sigma resets

    def estimate_position(self, vid: str):
        """Perceived position + uncertainty (backs GET /estimate/{id})."""
        p = self.perceived.get(vid)
        if not p:
            return None
        return {
            "vehicle_id": vid,
            "lat": p["lat"],
            "lon": p["lon"],
            "uncertainty_radius_m": p["sigma_m"],          # bounding circle (compat)
            "sigma_along_m": p["sigma_along_m"],           # ellipse semi-axis along heading
            "sigma_cross_m": p["sigma_cross_m"],           # ellipse semi-axis across heading
            "uncertainty_heading_deg": p["uncertainty_heading_deg"],
            "blackout_duration_sec": p["age_sec"],
            "submerged": p.get("submerged", False),
            "in_blackout": p.get("in_blackout", False),
        }

    # ------------------------------------------------------------------ #
    # Mutators used by the event system / REST API
    # ------------------------------------------------------------------ #
    def add_contact(self, contact: dict):
        self.contacts = [c for c in self.contacts if c.get("id") != contact.get("id")]
        contact = dict(contact)
        contact["detected_ts"] = time.time()
        self.contacts.append(contact)

    def remove_capability(self, vid: str, capability: str):
        p = self.perceived.get(vid)
        if p and capability in p.get("capabilities", []):
            p["capabilities"].remove(capability)
            if "sensor_health" in p:
                p["sensor_health"][capability] = 0.0
            if p["status"] == "nominal":
                p["status"] = "degraded"

    def set_status(self, vid: str, status: str):
        p = self.perceived.get(vid)
        if p:
            p["status"] = status

    def set_battery(self, vid: str, pct: float):
        p = self.perceived.get(vid)
        if p:
            p["battery_pct"] = pct

    def assign_task(self, vid: str, task: str, lat=None, lon=None):
        p = self.perceived.get(vid)
        if not p:
            return None
        p["current_task"] = task
        if lat is not None and lon is not None:
            if p["type"] in WATER_TYPES and not is_water(lat, lon):
                lat, lon = snap_to_water(lat, lon)
            p["waypoint"] = {"lat": lat, "lon": lon}
        return p

    def push_alert(self, alert: dict):
        alert = dict(alert)
        alert.setdefault("ts", time.time())
        alert.setdefault("sim_time_sec", self.sim_time_sec)
        self.alerts.append(alert)
        self.alerts = self.alerts[-10:]

    # ------------------------------------------------------------------ #
    # Snapshot (the WebSocket payload)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        return {
            "vehicles": list(self.perceived.values()),
            "contacts": self.contacts,
            "alerts": self.alerts,
            "sim_time_sec": self.sim_time_sec,
            "timestamp": time.time(),
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

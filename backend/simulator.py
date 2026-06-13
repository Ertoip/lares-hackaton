"""Synthetic telemetry generator for the maritime DSS prototype.

Two states are kept for every vehicle:

  * **Ground truth** (`self.vehicles`) — the vehicle's real state. The
    simulator advances this every tick. The operator never sees it directly.
  * **Perceived / reconstruction** (`self.perceived`) — the picture the DSS
    rebuilds purely from telemetry packets that actually arrive. Packets are
    dropped, delayed and noised by the comms model (see comms.py), so the
    perceived position dead-reckons forward from the last received fix and its
    uncertainty σ grows the longer a vehicle stays silent.

The gap between the two is the uncertainty the operator must see (decision
class D4). A submerged UUV is just the extreme case: its acoustic link goes
to near-total blackout, so perceived σ balloons until it surfaces.

Fleet: 6 unmanned vehicles (2 UAV, 2 USV, 2 UUV) in the Dover Strait.
"""

import math
import random
import time

from geo import is_water, snap_to_water
from comms import (
    CommsModel,
    drop_probability,
    base_sigma_m,
    sigma_growth_m_per_min,
    residual_bandwidth_kbps,
)

# Vehicle types that must stay on the water (surface + subsurface).
WATER_TYPES = {"USV", "UUV"}

# Update cadence per vehicle type (seconds between telemetry transmissions).
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

# A perceived fix older than (factor x update rate) is flagged stale.
STALE_FACTOR = 4

# Operating depth (z, metres) by type. UAVs fly, USVs sit at the surface,
# UUVs cruise submerged; `submerge()` takes a UUV deeper still.
DEFAULT_Z = {"UAV": 150.0, "USV": 0.0, "UUV": -40.0}
SUBMERGED_Z = -80.0

PAYLOAD_DEFAULTS = {"UAV": "sensors_active", "USV": "sensors_active", "UUV": "survey_active"}

# One nautical mile expressed in degrees of latitude.
NM_PER_DEG_LAT = 60.0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _knots_to_deg_per_sec_lat(speed_knots: float) -> float:
    """Convert speed in knots to degrees of latitude travelled per second."""
    nm_per_sec = speed_knots / 3600.0
    return nm_per_sec / NM_PER_DEG_LAT


# Initial fleet definition. All vehicles seeded inside the Dover Strait
# box (50.85-51.25 N, 0.90-2.20 E).
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


class Simulator:
    """Holds and advances ground truth + the operator's perceived picture."""

    def __init__(self, comms: CommsModel | None = None):
        self.start_time = time.time()
        self.comms = comms or CommsModel()
        self.vehicles = {}        # ground truth
        self.perceived = {}       # operator's reconstruction
        self.contacts = []        # threat contacts
        self.alerts = []          # recent alert records broadcast to the UI
        # Per-vehicle bookkeeping that is not part of the public state.
        self._last_update = {}    # last transmission-attempt time
        self._inflight = {}       # vid -> list of in-transit packets (delay)
        # Live data providers, attached by main.py (may stay None).
        self.weather = None
        self.ais = None

        now = time.time()
        for d in VEHICLE_DEFS:
            v = dict(d)
            # Water vehicles seeded on land are nudged to the nearest sea cell.
            if v["type"] in WATER_TYPES and not is_water(v["lat"], v["lon"]):
                v["lat"], v["lon"] = snap_to_water(v["lat"], v["lon"])
            v["z_m"] = DEFAULT_Z[v["type"]]
            v["link_type"] = LINK_TYPES[v["type"]]
            v["link_quality"] = round(random.uniform(0.85, 1.0), 2)
            v["last_contact_ts"] = now
            v["status"] = "nominal"
            v["submerged"] = False
            v["waypoint"] = None  # {"lat":..., "lon":...} when assigned
            v["sensor_health"] = {cap: 1.0 for cap in v["capabilities"]}
            v["payload_state"] = PAYLOAD_DEFAULTS[v["type"]]
            self._update_velocity(v)
            self.vehicles[v["id"]] = v
            self._last_update[v["id"]] = now
            self._inflight[v["id"]] = []
            self._init_perceived(v, now)

    # ------------------------------------------------------------------ #
    # Time helpers
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
        """Advance ground truth, transmit telemetry, refresh perceived state."""
        now = time.time()
        sea = self._sea_state()

        for vid, v in self.vehicles.items():
            elapsed = now - self._last_update[vid]
            if elapsed < UPDATE_RATES[v["type"]]:
                continue

            # Ground truth always advances — even submerged, the real vehicle
            # keeps moving; it just stops being heard from.
            self._move(v, elapsed)
            self._drain_battery(v, elapsed)
            self._update_velocity(v)
            self._update_link_quality(v, sea)
            self._last_update[vid] = now

            # Auto-mark bingo if battery is critically low (truth-side).
            if v["battery_pct"] <= 15 and v["status"] == "nominal":
                v["status"] = "bingo"

            # Attempt to transmit telemetry; the comms model may drop it.
            pkt = self.comms.transmit(v, sea_state=sea, now=now)
            if pkt is not None:
                self._inflight[vid].append(pkt)

        # Deliver any in-flight packets whose latency has elapsed, then
        # refresh every vehicle's perceived estimate (dead-reckon + grow σ).
        self._deliver_packets(now)
        for vid in self.vehicles:
            self._update_perceived_estimate(vid, now)

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

        # Small Gaussian position noise (true-state process noise).
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
        if not (50.85 <= v["lat"] <= 51.25):
            v["heading"] = (v["heading"] + 180) % 360
            v["lat"] = min(51.25, max(50.85, v["lat"]))
        if not (0.90 <= v["lon"] <= 2.20):
            v["heading"] = (v["heading"] + 180) % 360
            v["lon"] = min(2.20, max(0.90, v["lon"]))

        # Tiny heading drift so tracks aren't perfectly straight.
        v["heading"] = (v["heading"] + random.gauss(0, 1.5)) % 360

    def _drain_battery(self, v, elapsed):
        # Aerial vehicles burn faster than surface/subsurface.
        rate = 0.02 if v["type"] == "UAV" else 0.008
        v["battery_pct"] = round(max(0.0, v["battery_pct"] - rate * elapsed), 1)

    def _update_velocity(self, v):
        """Cartesian velocity vector (knots) from speed + heading (0 = North)."""
        hdg = math.radians(v["heading"])
        spd = v["speed_knots"]
        v["velocity"] = {
            "vx_kn": round(spd * math.sin(hdg), 3),   # east
            "vy_kn": round(spd * math.cos(hdg), 3),   # north
        }

    def _update_link_quality(self, v, sea):
        """Evolve the true link quality as a bounded random walk toward the
        level implied by current conditions (a link-quality time series)."""
        implied = 1.0 - drop_probability(
            v["link_type"], sea if sea is not None else 3,
            v.get("z_m", 0.0), v.get("submerged", False),
        )
        q = v["link_quality"] + (implied - v["link_quality"]) * 0.2 + random.gauss(0, 0.02)
        v["link_quality"] = round(_clamp(q, 0.0, 1.0), 2)

    # ------------------------------------------------------------------ #
    # Perceived-state reconstruction
    # ------------------------------------------------------------------ #
    def _init_perceived(self, v, now):
        """Seed perceived state from a fresh ground-truth fix at startup."""
        self.perceived[v["id"]] = {
            "id": v["id"], "type": v["type"], "link_type": v["link_type"],
            # last received fix (truth at t0)
            "recv_lat": v["lat"], "recv_lon": v["lon"], "recv_z_m": v["z_m"],
            "recv_heading": v["heading"], "recv_speed_knots": v["speed_knots"],
            "recv_ts": now,
            # last received scalar state
            "z_m": v["z_m"], "battery_pct": v["battery_pct"], "status": v["status"],
            "submerged": v["submerged"], "capabilities": list(v["capabilities"]),
            "sensor_health": dict(v["sensor_health"]), "payload_state": v["payload_state"],
            "link_quality": v["link_quality"], "current_task": v["current_task"],
            "waypoint": v["waypoint"],
        }
        self._update_perceived_estimate(v["id"], now)

    def _deliver_packets(self, now):
        """Apply in-flight packets whose latency has elapsed (latest fix wins)."""
        for vid, queue in self._inflight.items():
            arrived = [pk for pk in queue if pk["arrive_ts"] <= now]
            if not arrived:
                continue
            self._inflight[vid] = [pk for pk in queue if pk["arrive_ts"] > now]
            latest = max(arrived, key=lambda pk: pk["sent_ts"])
            self._apply_packet(vid, latest)

    def _apply_packet(self, vid, pkt):
        p = self.perceived[vid]
        f = pkt["fields"]
        p["recv_lat"] = f["lat"]
        p["recv_lon"] = f["lon"]
        p["recv_z_m"] = f["z_m"]
        p["recv_heading"] = f["heading"]
        p["recv_speed_knots"] = f["speed_knots"]
        p["recv_ts"] = pkt["sent_ts"]
        # Last received scalar state.
        p["z_m"] = f["z_m"]
        p["battery_pct"] = f["battery_pct"]
        p["status"] = f["status"]
        p["submerged"] = f["submerged"]
        p["capabilities"] = f["capabilities"]
        p["sensor_health"] = f["sensor_health"]
        p["payload_state"] = f["payload_state"]
        p["link_quality"] = f["link_quality"]
        p["current_task"] = f["current_task"]
        p["waypoint"] = f["waypoint"]

    def _update_perceived_estimate(self, vid, now):
        """Dead-reckon perceived position forward and grow uncertainty σ.

        This is the single code path for *all* vehicles — a fresh UAV fix and a
        long-submerged UUV blackout differ only in how stale the last fix is.
        """
        p = self.perceived[vid]
        age = max(0.0, now - p["recv_ts"])

        step_lat = _knots_to_deg_per_sec_lat(p["recv_speed_knots"]) * age
        hdg = math.radians(p["recv_heading"])
        dlat = step_lat * math.cos(hdg)
        coslat = max(0.1, math.cos(math.radians(p["recv_lat"])))
        dlon = (step_lat * math.sin(hdg)) / coslat

        p["lat"] = round(p["recv_lat"] + dlat, 6)
        p["lon"] = round(p["recv_lon"] + dlon, 6)
        p["heading"] = p["recv_heading"]
        p["velocity"] = {
            "vx_kn": round(p["recv_speed_knots"] * math.sin(hdg), 3),
            "vy_kn": round(p["recv_speed_knots"] * math.cos(hdg), 3),
        }

        link = p["link_type"]
        sigma = base_sigma_m(link) + sigma_growth_m_per_min(link) * (age / 60.0)
        p["sigma_m"] = round(sigma, 1)
        p["age_sec"] = round(age, 1)
        p["last_contact_ts"] = p["recv_ts"]
        p["stale"] = age > STALE_FACTOR * UPDATE_RATES[p["type"]]
        # The operator can't see a "submerged" bit during blackout — they infer
        # loss of contact from staleness. This is what drives the UI cone.
        p["in_blackout"] = p["stale"]
        p["residual_bandwidth_kbps"] = residual_bandwidth_kbps(
            link, p.get("link_quality") or 0.0
        )
        # Rough ETA to the next packet; unknown while in acoustic blackout.
        if p.get("submerged"):
            p["expected_next_contact_sec"] = None
        else:
            p["expected_next_contact_sec"] = round(
                max(0.0, UPDATE_RATES[p["type"]] - age), 1
            )

    # ------------------------------------------------------------------ #
    # Submerge / surface (acoustic blackout is just an extreme of comms)
    # ------------------------------------------------------------------ #
    def submerge(self, vid: str):
        v = self.vehicles.get(vid)
        if not v:
            return
        v["submerged"] = True
        v["z_m"] = SUBMERGED_Z
        if v["status"] == "nominal":
            v["status"] = "lost_comms"

    def surface(self, vid: str):
        v = self.vehicles.get(vid)
        if not v:
            return
        v["submerged"] = False
        v["z_m"] = DEFAULT_Z[v["type"]]
        v["status"] = "nominal"
        v["last_contact_ts"] = time.time()

    def estimate_position(self, vid: str):
        """Return the perceived position + uncertainty for any vehicle.

        Backs GET /estimate/{id}. Fields kept backward-compatible with the UI:
        `uncertainty_radius_m` is the perceived σ, `blackout_duration_sec` is
        the age of the last received fix.
        """
        p = self.perceived.get(vid)
        if not p:
            return None
        return {
            "vehicle_id": vid,
            "lat": p["lat"],
            "lon": p["lon"],
            "uncertainty_radius_m": p["sigma_m"],
            "blackout_duration_sec": p["age_sec"],
            "submerged": p.get("submerged", False),
            "in_blackout": p.get("in_blackout", False),
        }

    # ------------------------------------------------------------------ #
    # Mutators used by the event system / REST API (act on GROUND TRUTH)
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
            # Degradation is visible in telemetry, not just the capability list.
            if "sensor_health" in v:
                v["sensor_health"][capability] = 0.0
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
            # What the operator's DSS sees (reconstruction).
            "vehicles": list(self.perceived.values()),
            # The real state, for the truth-vs-reconstruction overlay / debug.
            "ground_truth": list(self.vehicles.values()),
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

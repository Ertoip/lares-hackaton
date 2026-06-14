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

# Battery drain rate (% per second) by type. UAVs drain fastest due to high
# power demand; UUVs drain slowest (large battery cells, low propulsion load).
DRAIN_RATE_PCT_PER_SEC = {"UAV": 0.02, "USV": 0.008, "UUV": 0.006}

# Battery bingo threshold (%) by type. UAVs need fuel reserve for return-to-base.
BINGO_PCT = {"UAV": 25.0, "USV": 20.0, "UUV": 30.0}

# Pre-bingo warning fires this many % above the bingo threshold, giving the
# operator lead time to react before a vehicle actually reaches bingo.
PREBINGO_MARGIN_PCT = 10.0

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

# Inertial-navigation fallback (GPS jamming): sigma grows at this rate with no
# external fix, reflecting accumulated IMU drift (~50 m/min per scenario brief).
GPS_JAMMING_DRIFT_M_PER_MIN = 50.0

# Operating-area box (Dover Strait).
LAT_MIN, LAT_MAX = 50.85, 51.25
LON_MIN, LON_MAX = 0.90, 2.20

# The mothership: the crewed command vessel the fleet operates from. It is NOT
# an unmanned, comms-tracked vehicle — its position is simply known, so it has
# no uncertainty cone, no telemetry rolls and never appears in `perceived`. It
# just steams slowly across the area.
MOTHERSHIP_DEF = {
    "id": "CSV MERIDIAN", "type": "mothership",
    # Kept below the slowest UUV (3.5 kn) so a returning sub can always close the
    # gap and dock — a faster ship would simply outrun a bingo'd UUV's pursuit.
    "lat": 51.05, "lon": 1.55, "heading": 95.0, "speed_knots": 2.5,
}

# A returning vehicle within this distance of the mothership is considered
# docked (battery swap / recharge). Larger than the 150 m waypoint-arrival
# radius so docking resolves before the generic "arrived" logic fires.
DOCK_RADIUS_M = 400.0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _knots_to_deg_per_sec_lat(speed_knots: float) -> float:
    """Convert speed in knots to degrees of latitude travelled per second."""
    return (speed_knots / 3600.0) / NM_PER_DEG_LAT


# Initial fleet definition. All vehicles seeded inside the Dover Strait box.
VEHICLE_DEFS = [
    {
        "id": "UAV-1", "type": "UAV", "lat": 51.14, "lon": 1.32,
        "heading": 120.0, "speed_knots": 60.0, "battery_pct": 78.0,
        "capabilities": ["visual_ISR", "thermal_ISR"],
        "current_task": "Wide-area ISR patrol",
    },
    {
        "id": "UAV-2", "type": "UAV", "lat": 50.96, "lon": 1.85,
        "heading": 300.0, "speed_knots": 55.0, "battery_pct": 91.0,
        "capabilities": ["visual_ISR", "comms_relay"],
        "current_task": "Comms relay orbit",
    },
    {
        "id": "USV-1", "type": "USV", "lat": 51.12, "lon": 1.45,
        "heading": 45.0, "speed_knots": 18.0, "battery_pct": 84.0,
        "capabilities": ["surface_radar", "active_sonar"],
        "current_task": "Picket line patrol",
    },
    {
        "id": "USV-2", "type": "USV", "lat": 50.96, "lon": 1.78,
        "heading": 210.0, "speed_knots": 14.0, "battery_pct": 88.0,
        "capabilities": ["surface_radar", "passive_sonar"],
        "current_task": "ASW screen",
    },
    {
        "id": "UUV-1", "type": "UUV", "lat": 51.10, "lon": 1.62,
        "heading": 90.0, "speed_knots": 4.0, "battery_pct": 73.0,
        "capabilities": ["passive_sonar", "seabed_mapping"],
        "current_task": "Subsurface survey",
    },
    {
        "id": "UUV-2", "type": "UUV", "lat": 50.97, "lon": 1.45,
        "heading": 270.0, "speed_knots": 3.5, "battery_pct": 66.0,
        "capabilities": ["passive_sonar", "mine_countermeasures"],
        "current_task": "MCM sweep",
    },
]


class PerceivedSimulator:
    """Advances the operator's perceived picture directly (single state)."""

    def __init__(self):
        # Fast-forward offset (seconds) added to the real clock. Bumping this
        # makes the whole sim — motion, uncertainty growth, the event scheduler
        # — jump ahead so scripted events fire naturally without real waiting.
        self.clock_offset = 0.0
        self.start_time = time.time()
        self.perceived = {}       # vid -> estimate record (the only state)
        self.contacts = []        # threat contacts
        self.alerts = []          # recent alert records broadcast to the UI
        self._last_attempt = {}   # vid -> last fix-attempt time
        self._prebingo_warned = set()  # vids already pre-bingo-warned (fire once)
        # Live data providers, attached by main.py (may stay None).
        self.weather = None
        self.ais = None

        now = self.now()
        for d in VEHICLE_DEFS:
            self._seed(dict(d), now)
        self._seed_mothership(now)

    def now(self) -> float:
        """Simulation wall-clock: real time plus any fast-forward offset."""
        return time.time() + self.clock_offset

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
            "rtb": False,  # auto return-to-ship in progress (set on bingo)
            "eta_to_ship_sec": None,  # ETA to mothership while rtb (else None)
        }
        self.perceived[vid] = p
        self._last_attempt[vid] = now
        self._update_estimate(p, now)

    # ------------------------------------------------------------------ #
    # Time / environment helpers
    # ------------------------------------------------------------------ #
    @property
    def sim_time_sec(self) -> int:
        return int(self.now() - self.start_time)

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
        now = self.now()
        sea = self._sea_state()

        # Move the ship first so any returning vehicle steers toward its fresh
        # position this tick.
        self._move_mothership(now)

        for vid, p in self.perceived.items():
            # A returning vehicle re-aims at the (moving) ship every tick.
            if p.get("rtb"):
                p["waypoint"] = {"lat": self.mothership["lat"], "lon": self.mothership["lon"]}
            if now - self._last_attempt[vid] >= UPDATE_RATES[p["type"]]:
                self._attempt_contact(p, now, sea)
                self._last_attempt[vid] = now
            # Every tick: dead-reckon the estimate forward and grow sigma.
            self._update_estimate(p, now)
            if p.get("rtb"):
                self._update_rtb_eta(p)
                self._check_docking(p)
            else:
                p["eta_to_ship_sec"] = None

    # ------------------------------------------------------------------ #
    # Mothership (crewed command vessel — known position, no telemetry)
    # ------------------------------------------------------------------ #
    def _seed_mothership(self, now):
        d = MOTHERSHIP_DEF
        lat, lon = d["lat"], d["lon"]
        if not is_water(lat, lon):
            lat, lon = snap_to_water(lat, lon)
        self.mothership = {
            "id": d["id"], "type": "mothership",
            "lat": round(lat, 6), "lon": round(lon, 6),
            "heading": d["heading"], "speed_knots": d["speed_knots"],
        }
        self._mothership_ts = now

    def _move_mothership(self, now):
        """Steam slowly along heading; turn away from land / the box edge."""
        m = self.mothership
        dt = max(0.0, now - self._mothership_ts)
        self._mothership_ts = now
        if dt <= 0:
            return
        step_lat = _knots_to_deg_per_sec_lat(m["speed_knots"]) * dt
        hdg = math.radians(m["heading"])
        new_lat = m["lat"] + step_lat * math.cos(hdg)
        coslat = max(0.1, math.cos(math.radians(m["lat"])))
        new_lon = m["lon"] + (step_lat * math.sin(hdg)) / coslat
        blocked = (
            not is_water(new_lat, new_lon)
            or not (LAT_MIN <= new_lat <= LAT_MAX)
            or not (LON_MIN <= new_lon <= LON_MAX)
        )
        if blocked:
            m["heading"] = (m["heading"] + 150 + random.uniform(-30, 30)) % 360
            return
        m["lat"], m["lon"] = round(new_lat, 6), round(new_lon, 6)

    # ------------------------------------------------------------------ #
    # Return-to-ship + recharge (auto-triggered on bingo)
    # ------------------------------------------------------------------ #
    def trigger_bingo(self, vid, battery_pct=None, push_alert=True):
        """Single entry point for entering bingo, used by both the organic
        battery-drain path and the manual ``bingo_warning`` injection. Keeps the
        two from diverging (the inject used to skip the auto-return)."""
        p = self.perceived.get(vid)
        if not p:
            return
        if battery_pct is not None:
            p["battery_pct"] = battery_pct
        p["status"] = "bingo"
        # A bingo supersedes its own pre-bingo warning — drop the stale one.
        self.alerts = [
            a for a in self.alerts
            if not (a.get("type") == "prebingo_warning" and a.get("vehicle") == vid)
        ]
        if push_alert:
            self.push_alert({
                "type": "bingo_warning",
                "vehicle": vid,
                "message": f"{vid} BINGO fuel — battery at {p['battery_pct']:.0f}% — returning to ship",
            })
        self._begin_rtb(p)

    def _begin_rtb(self, p):
        """Send a bingo'd vehicle home. Submerged UUVs surface first so they can
        navigate to the ship and dock; status stays `bingo` until recharged."""
        if p.get("submerged"):
            # Un-submerge WITHOUT calling surface() (which would reset status to
            # nominal and clear the bingo we just set).
            p["submerged"] = False
            p["z_m"] = DEFAULT_Z[p["type"]]
            p["last_contact_ts"] = self.now()
        p["rtb"] = True
        p["current_task"] = "RTB — returning to ship for recharge"
        p["waypoint"] = {"lat": self.mothership["lat"], "lon": self.mothership["lon"]}

    def _update_rtb_eta(self, p):
        """ETA (sec) to the mothership, accounting for both velocities.

        The vehicle heads straight at the ship, so its full speed closes the
        range; the ship's velocity only counts by its component *along* the
        vehicle->ship line (it helps if steaming toward the vehicle, hurts if
        fleeing). closing = v_vehicle - (ship velocity . bearing-to-ship)."""
        m = self.mothership
        dist_m = self._haversine_m(p["lat"], p["lon"], m["lat"], m["lon"])
        brg = math.radians(self._bearing(p["lat"], p["lon"], m["lat"], m["lon"]))
        dn, de = math.cos(brg), math.sin(brg)  # vehicle->ship unit (north, east)
        shdg = math.radians(m["heading"])
        ship_radial = (
            m["speed_knots"] * math.cos(shdg) * dn
            + m["speed_knots"] * math.sin(shdg) * de
        )
        closing_kn = p["speed_knots"] - ship_radial
        if closing_kn <= 0.05:
            p["eta_to_ship_sec"] = None  # not gaining on the ship
            return
        p["eta_to_ship_sec"] = round(dist_m / (closing_kn * KN_TO_MPS))

    def _check_docking(self, p):
        """Dock + recharge once the vehicle reaches the mothership."""
        d = self._haversine_m(
            p["fix_lat"], p["fix_lon"], self.mothership["lat"], self.mothership["lon"]
        )
        if d <= DOCK_RADIUS_M:
            self._dock(p)

    def _dock(self, p):
        p["battery_pct"] = 100.0
        p["status"] = "nominal"
        p["rtb"] = False
        p["waypoint"] = None
        p["eta_to_ship_sec"] = None
        self._prebingo_warned.discard(p["id"])
        # Recharged and idle at the ship. We don't pick its next job here — the
        # DSS does. Mark it awaiting tasking and emit an alert the bridge forwards
        # as a DSS event, closing the loop: dock -> event -> DSS decides ->
        # reassign_task command -> POST /command -> assign_task.
        p["current_task"] = "Awaiting tasking"
        self.push_alert({
            "type": "vehicle_docked",
            "vehicle": p["id"],
            "message": f"{p['id']} docked at {self.mothership['id']} — recharged to 100%",
        })
        self.push_alert({
            "type": "awaiting_tasking",
            "vehicle": p["id"],
            "message": f"{p['id']} recharged and idle at {self.mothership['id']} — awaiting DSS tasking",
        })

    def _attempt_contact(self, p, now, sea):
        """One telemetry cycle: maybe receive a fresh fix; always age scalars."""
        # Battery + link quality evolve whether or not the packet gets through.
        self._drain_battery(p, now)
        self._update_link_quality(p, sea)
        if p["battery_pct"] <= BINGO_PCT[p["type"]] and p["status"] != "bingo":
            self.trigger_bingo(p["id"])

        # Pre-bingo warning: lead-time heads-up before reaching bingo. Fires for
        # already-troubled vehicles too (a low battery on top of another fault
        # is exactly when it matters); only a vehicle already AT bingo is spared
        # the redundant "approaching" message. Re-arms if battery recovers.
        prebingo = BINGO_PCT[p["type"]] + PREBINGO_MARGIN_PCT
        if p["battery_pct"] <= prebingo and p["status"] != "bingo":
            if p["id"] not in self._prebingo_warned:
                self._prebingo_warned.add(p["id"])
                self.push_alert({
                    "type": "prebingo_warning",
                    "vehicle": p["id"],
                    "message": f"{p['id']} battery {p['battery_pct']:.0f}% — approaching "
                               f"bingo ({BINGO_PCT[p['type']]:.0f}%)",
                })
        else:
            self._prebingo_warned.discard(p["id"])

        # Does a fresh fix arrive this cycle? (submerged acoustic ~ never)
        sea_val = sea if sea is not None else 3
        p_drop = drop_probability(
            p["link_type"], sea_val, p.get("z_m", 0.0), p.get("submerged", False)
        )
        if random.random() >= p_drop and not p.get("gps_jammed"):
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
        # Keep full precision: per-cycle drain (e.g. 0.02 %/s x 0.5 s = 0.01%) is
        # smaller than one rounding step, so round(..., 1) here would erase it
        # every cycle and the battery would never move. Rounding is for display.
        elapsed = now - self._last_attempt[p["id"]]
        rate = DRAIN_RATE_PCT_PER_SEC[p["type"]]
        p["battery_pct"] = max(0.0, p["battery_pct"] - rate * elapsed)

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
        if p.get("gps_jammed"):
            # Inertial navigation: sigma grows isotropically at the IMU drift rate;
            # no GPS fix means no anchor reset, so uncertainty is purely time-driven.
            sigma_along = base + GPS_JAMMING_DRIFT_M_PER_MIN * age_min
            sigma_cross = sigma_along
        else:
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
        p["last_contact_ts"] = self.now()  # re-established contact -> sigma resets

    def set_depth(self, vid: str, z_m: float):
        """Commanded operating depth/altitude (metres relative to the surface:
        negative = depth below, positive = altitude above). Feeds the comms model
        via ``z_m``, so a deeper UUV gets a worse acoustic link automatically. A
        UUV taken below the surface is marked ``submerged`` (acoustic blackout);
        brought to ~0 it is treated as surfaced."""
        p = self.perceived.get(vid)
        if not p:
            return
        p["z_m"] = z_m
        p["fix_z_m"] = z_m
        if p["type"] == "UUV":
            if z_m < -1.0:
                p["submerged"] = True
                if p["status"] == "nominal":
                    p["status"] = "lost_comms"
            else:
                p["submerged"] = False
                if p["status"] == "lost_comms":
                    p["status"] = "nominal"
                p["last_contact_ts"] = self.now()  # back at the surface -> sigma resets

    def hold(self, vid: str):
        """Loiter in place: stash current speed and stop. The dead-reckoned
        estimate freezes (speed 0), so the vehicle holds station until ``resume``."""
        p = self.perceived.get(vid)
        if not p:
            return
        p.setdefault("_hold_speed", p["fix_speed_knots"])
        p["fix_speed_knots"] = 0.0
        if p["status"] == "nominal":
            p["status"] = "holding"

    def resume(self, vid: str):
        """Resume from a hold: restore the speed stashed by ``hold``."""
        p = self.perceived.get(vid)
        if not p:
            return
        if "_hold_speed" in p:
            p["fix_speed_knots"] = p.pop("_hold_speed")
        if p["status"] == "holding":
            p["status"] = "nominal"

    def return_to_ship(self, vid: str):
        """Commanded return-to-ship (no bingo battery semantics). Reuses the same
        homing+dock machinery as an automatic bingo RTB; status is ``rtb`` and is
        reset to ``nominal`` by ``_dock`` on arrival."""
        p = self.perceived.get(vid)
        if not p:
            return
        p["status"] = "rtb"
        self._begin_rtb(p)

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
        contact["detected_ts"] = self.now()
        self.contacts.append(contact)

    def remove_capability(self, vid: str, capability: str):
        p = self.perceived.get(vid)
        if p and capability in p.get("capabilities", []):
            p["capabilities"].remove(capability)
            if "sensor_health" in p:
                p["sensor_health"][capability] = 0.0
            if p["status"] == "nominal":
                p["status"] = "degraded"

    def start_gps_jamming(self, vid: str):
        """Switch vehicle to inertial-navigation fallback (GPS denied).

        Freezes GPS fix arrival so sigma grows at GPS_JAMMING_DRIFT_M_PER_MIN
        (~50 m/min per scenario brief). The last good GPS fix becomes the
        dead-reckoning anchor; position uncertainty grows from that moment.
        """
        p = self.perceived.get(vid)
        if not p:
            return
        p["gps_jammed"] = True
        if p["status"] == "nominal":
            p["status"] = "degraded"

    def stop_gps_jamming(self, vid: str):
        """Restore GPS — next comms cycle will deliver a fresh fix and reset sigma."""
        p = self.perceived.get(vid)
        if not p:
            return
        p["gps_jammed"] = False
        if p["status"] == "degraded":
            p["status"] = "nominal"
        p["last_contact_ts"] = self.now()  # treat GPS re-acquisition as a new fix

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
        alert.setdefault("ts", self.now())
        alert.setdefault("sim_time_sec", self.sim_time_sec)
        self.alerts.append(alert)
        self.alerts = self.alerts[-10:]

    # ------------------------------------------------------------------ #
    # Snapshot (the WebSocket payload)
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        return {
            "vehicles": list(self.perceived.values()),
            "mothership": self.mothership,
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

"""Comms / transmission model for the maritime DSS prototype.

Sits between ground truth (what a vehicle actually is) and the operator's
reconstructed picture (what the DSS can infer from received telemetry). Each
tick, for each vehicle, this model decides:

  * whether a telemetry packet makes it back at all (stochastic drop,
    conditional on link type, sea state and depth),
  * how long it takes to arrive (link latency), and
  * how much measurement noise the reported position carries.

The three domains have genuinely different physics, which is the whole point
the jury wants to see:

  * radio (UAV)      — fast, reliable, high bandwidth.
  * satellite (USV)  — seconds of latency, occasional drops.
  * acoustic (UUV)   — tens of seconds latency, drops badly in rough seas and
                       at depth, and goes fully silent while submerged.

The module-level functions are pure (no app state) so they can be unit-tested
directly; `CommsModel` is the thin object the simulator holds.
"""

import math
import random
import time

# Per-link-type transmission profiles. Tune with the mentor; these are
# deliberately conservative starting points.
LINK_PROFILES = {
    "radio": {
        "base_drop_p": 0.02,
        "k_sea": 0.05,            # extra drop prob at the roughest sea
        "k_depth": 0.0,           # radio doesn't care about depth (surface/air)
        "latency_range": (0.1, 0.5),
        "sigma_meas_m": 5.0,      # position measurement noise (1-sigma)
        "sigma_growth_m_per_min": 8.0,   # σ growth while no packet arrives
        "max_bandwidth_kbps": 2000.0,
    },
    "satellite": {
        "base_drop_p": 0.08,
        "k_sea": 0.15,
        "k_depth": 0.0,
        "latency_range": (1.0, 3.0),
        "sigma_meas_m": 10.0,
        "sigma_growth_m_per_min": 15.0,
        "max_bandwidth_kbps": 500.0,
    },
    "acoustic": {
        "base_drop_p": 0.25,
        "k_sea": 0.30,
        "k_depth": 0.40,
        "latency_range": (10.0, 40.0),
        "sigma_meas_m": 20.0,
        "sigma_growth_m_per_min": 60.0,
        "max_bandwidth_kbps": 5.0,
    },
}

DEFAULT_SEA_STATE = 3       # used when no live weather is available
MAX_SEA_STATE = 9           # Douglas scale ceiling
MAX_DEPTH_M = 300.0         # depth at which the acoustic depth term saturates
MAX_DROP_P = 0.95           # never claim a link is 100% dead (except blackout)
SUBMERGED_DROP_P = 0.98     # acoustic blackout while submerged (Scenario A)

M_PER_DEG_LAT = 111320.0    # metres per degree of latitude


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def drop_probability(link_type, sea_state, depth_m=0.0, submerged=False):
    """Probability that a telemetry packet is lost this transmission.

    Rises with sea state (acoustic worst) and, for acoustic links, with depth.
    A submerged vehicle on an acoustic link is effectively in blackout.
    """
    prof = LINK_PROFILES[link_type]
    if submerged and link_type == "acoustic":
        return SUBMERGED_DROP_P

    sea_norm = _clamp(sea_state / MAX_SEA_STATE, 0.0, 1.0)
    depth_norm = _clamp(abs(depth_m) / MAX_DEPTH_M, 0.0, 1.0)
    p = (prof["base_drop_p"]
         + prof["k_sea"] * sea_norm
         + prof["k_depth"] * depth_norm)
    return _clamp(p, 0.0, MAX_DROP_P)


def residual_bandwidth_kbps(link_type, link_quality):
    """Usable bandwidth = link ceiling scaled by current link quality (0..1)."""
    ceiling = LINK_PROFILES[link_type]["max_bandwidth_kbps"]
    return round(ceiling * _clamp(link_quality, 0.0, 1.0), 1)


def base_sigma_m(link_type):
    """Position measurement noise (1-sigma, metres) for a fresh fix."""
    return LINK_PROFILES[link_type]["sigma_meas_m"]


def sigma_growth_m_per_min(link_type):
    """How fast the position uncertainty grows with no fresh fix (m/min)."""
    return LINK_PROFILES[link_type]["sigma_growth_m_per_min"]


def _add_pos_noise(lat, lon, sigma_m):
    """Return (lat, lon) perturbed by Gaussian noise of 1-sigma `sigma_m`."""
    dlat = random.gauss(0, sigma_m) / M_PER_DEG_LAT
    coslat = max(0.1, math.cos(math.radians(lat)))
    dlon = random.gauss(0, sigma_m) / (M_PER_DEG_LAT * coslat)
    return round(lat + dlat, 6), round(lon + dlon, 6)


class CommsModel:
    """Decides per-vehicle packet delivery, latency and measurement noise."""

    def transmit(self, v: dict, sea_state=None, now=None):
        """Attempt to transmit a telemetry packet for ground-truth vehicle `v`.

        Returns a packet dict (to be queued until `arrive_ts`) or None if the
        packet was dropped. The packet carries a *noisy* copy of the truth —
        the operator never sees the exact position.
        """
        now = time.time() if now is None else now
        link = v["link_type"]
        sea = DEFAULT_SEA_STATE if sea_state is None else sea_state

        p = drop_probability(link, sea, v.get("z_m", 0.0), v.get("submerged", False))
        if random.random() < p:
            return None  # dropped — operator hears nothing this cycle

        lo, hi = LINK_PROFILES[link]["latency_range"]
        latency = random.uniform(lo, hi)
        sigma = base_sigma_m(link)
        noisy_lat, noisy_lon = _add_pos_noise(v["lat"], v["lon"], sigma)

        fields = {
            "lat": noisy_lat,
            "lon": noisy_lon,
            "z_m": v.get("z_m", 0.0),
            "heading": v["heading"],
            "speed_knots": v["speed_knots"],
            "battery_pct": v["battery_pct"],
            "status": v["status"],
            "submerged": v.get("submerged", False),
            "capabilities": list(v.get("capabilities", [])),
            "sensor_health": dict(v.get("sensor_health", {})),
            "payload_state": v.get("payload_state"),
            "link_quality": v.get("link_quality"),
            "current_task": v.get("current_task"),
            "waypoint": v.get("waypoint"),
        }
        return {
            "vid": v["id"],
            "sent_ts": now,
            "arrive_ts": now + latency,
            "sigma_meas_m": sigma,
            "fields": fields,
        }

# Maritime DSS Prototype

A decision-support-system prototype for unmanned maritime operations in the
**Dover Strait**. The backend simulates the **operator's perceived picture** of
a 6-vehicle fleet and streams it over a WebSocket; a React + Leaflet UI renders
it, and a **DSS solver** consumes the same stream to recommend actions.

> **Key design choice — perceived-only.** There is no hidden "ground truth"
> state. The simulator produces *only what the operator (and therefore the DSS)
> can actually know*: a best-estimate position per vehicle plus an explicit,
> growing **uncertainty cone**. This is the correct input for decision support —
> the solver never sees a position the real system couldn't have known.

## Components

1. **Perceived telemetry simulator** (`backend/perceived_sim.py`) — the active
   simulator. 6 unmanned vehicles (2 UAV, 2 USV, 2 UUV) moving in real time.
   Each has one estimated position; a per-cycle comms-availability roll decides
   whether a fresh fix arrives. No fix → the estimate **dead-reckons** forward
   and its uncertainty grows into a **cone**; fix arrives → the anchor advances
   and uncertainty resets to the link baseline.
2. **Comms physics** (`backend/comms.py`) — pure helper functions for per-link
   drop probability, baseline position noise and uncertainty growth (radio /
   satellite / acoustic differ sharply).
3. **Event timelines** (`backend/events.py`) — scripted scenarios that fire on
   the sim clock and mutate vehicle state, plus manual injection.
4. **FastAPI server + WebSocket broadcast** (`backend/main.py`).
5. **React + Leaflet map UI** (`frontend/src/App.jsx`).

Live external data is layered on top:

- **Open-Meteo** (`backend/weather.py`) — real marine + atmospheric conditions
  (waves, wind, air temp, Douglas sea state). Sea state feeds back into comms
  drop rates. Key-less.
- **AISStream.io** (`backend/ais.py`) — real-world vessel traffic over WebSocket,
  drawn in grey. Requires a free API key; dormant without one.
- **Land/water masking** (`backend/geo.py`) — keeps surface/subsurface vehicles
  on the water.

> `backend/simulator.py` is the **legacy two-state** (ground-truth +
> reconstruction) simulator, kept only as a fallback. The running server uses
> `perceived_sim.py`.

```
.
├── backend/
│   ├── perceived_sim.py  # ACTIVE simulator: perceived estimate + cone
│   ├── comms.py          # per-link drop / noise / uncertainty-growth physics
│   ├── events.py         # SCENARIO_A / SCENARIO_B timelines + scheduler
│   ├── geo.py            # land/water mask
│   ├── weather.py        # Open-Meteo poller
│   ├── ais.py            # AISStream.io live vessel-traffic client
│   ├── main.py           # FastAPI app, WebSocket, REST
│   ├── simulator.py      # legacy two-state sim (unused fallback)
│   └── requirements.txt
└── frontend/
    └── src/App.jsx       # map + sidebar
```

## Prerequisites

- Python 3.10+ (uses `X | None` type syntax)
- Node.js 18+

## 1. Start the backend (port 8000)

```bash
cd backend
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# macOS/Linux:         source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Quick checks: `GET /vehicles`, `GET /estimate/UUV-1`, WebSocket `ws://localhost:8000/ws`.

### Optional: real AIS traffic

```bash
# Windows PowerShell:  $env:AISSTREAM_API_KEY = "your-key-here"
# macOS/Linux:         export AISSTREAM_API_KEY=your-key-here
uvicorn main:app --reload
```

## 2. Start the frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

---

# Data contract (for the DSS solver)

This is the **stable interface** between the simulation and the DSS. Everything
the solver needs arrives in one JSON object, pushed over `ws://localhost:8000/ws`
**once per second**. The same per-vehicle objects are also available via
`GET /vehicles`.

## Top-level snapshot

| Key | Type | Meaning |
|---|---|---|
| `vehicles` | array&lt;Vehicle&gt; | perceived estimate for each of the 6 vehicles (below) |
| `contacts` | array&lt;Contact&gt; | active threat contacts |
| `alerts` | array&lt;Alert&gt; | recent alert records (last 10) |
| `sim_time_sec` | int | seconds since simulation start |
| `timestamp` | float | server wall-clock (Unix seconds) at snapshot generation |
| `weather` | Weather \| null | latest Open-Meteo conditions |
| `ais` | array&lt;Vessel&gt; | live AIS traffic (`[]` if no API key) |

## Vehicle object — the core DSS input

Every field is what the operator *believes*, never hidden truth. Units are
explicit so the solver can consume them directly.

| Field | Type | Unit | Meaning / DSS use |
|---|---|---|---|
| `id` | str | — | `"UAV-1"`, `"USV-2"`, `"UUV-1"`, … |
| `type` | str | — | `UAV` / `USV` / `UUV` |
| `link_type` | str | — | `radio` / `satellite` / `acoustic` |
| **`lat`** | float | ° | **best-estimate latitude** (dead-reckoned from last fix) |
| **`lon`** | float | ° | **best-estimate longitude** |
| `z_m` | float | m | depth/altitude (negative = submerged, positive = airborne) |
| `heading` | float | ° (0=N, CW) | estimated heading |
| `speed_knots` | float | kn | estimated speed |
| `velocity` | obj | kn | `{vx_kn (east+), vy_kn (north+)}` — Cartesian velocity |
| **`sigma_m`** | float | m | **bounding-circle 1σ radius** = `max(along, cross)`. Use this if you want a single scalar uncertainty. |
| **`sigma_along_m`** | float | m | 1σ uncertainty **along** `heading` (speed/timing) |
| **`sigma_cross_m`** | float | m | 1σ uncertainty **across** `heading` (heading drift) |
| **`uncertainty_heading_deg`** | float | ° | orientation of the uncertainty ellipse = `heading` |
| `age_sec` | float | s | time since the last received fix |
| `last_contact_ts` | float | Unix s | timestamp of the last received fix |
| `stale` | bool | — | fix older than 4× the link's update cycle |
| `in_blackout` | bool | — | same as `stale`; drives the UI uncertainty cone |
| `expected_next_contact_sec` | float \| null | s | ETA to next fix; `null` while submerged |
| `residual_bandwidth_kbps` | float | kbps | usable comms bandwidth right now |
| `link_quality` | float | 0–1 | current link quality |
| `battery_pct` | float | % | remaining battery/fuel |
| `status` | str | — | `nominal` / `degraded` / `lost_comms` / `bingo` |
| `submerged` | bool | — | UUV is below periscope/acoustic-contact depth |
| `capabilities` | array&lt;str&gt; | — | currently-working sensors/payloads |
| `sensor_health` | obj | 0–1 | `{capability: health}`; `0.0` = failed |
| `payload_state` | str | — | e.g. `sensors_active`, `survey_active` |
| `current_task` | str | — | human-readable current tasking |
| `waypoint` | obj \| null | — | `{lat, lon}` if a destination is assigned |

> Records also carry internal anchor fields (`fix_lat`, `fix_lon`,
> `fix_heading`, `fix_speed_knots`, `fix_z_m`). **The solver should ignore
> these** and use `lat`/`lon` + the `sigma_*` fields. They are the dead-reckoning
> anchor, not a decision input.

### The uncertainty cone (important)

When a vehicle is heard from, uncertainty is a small circle. When it goes
silent, the uncertainty becomes an **ellipse aligned with the last heading** and
grows:

```
sigma_along = base + growth_per_min × (age/60)            # forward/back, time-driven
sigma_cross = base + (speed × age) × tan(drift_angle)     # sideways, distance × drift
drift_angle = min(45°, 6°/min × (age/60))
```

Early after a dropout the ellipse points **forward** (you know roughly *where on
the track*, not *how far*); over time the cross-track term overtakes and it
**fans into a cone** — the search area the operator/DSS must cover. A submerged
UUV is the extreme case. To draw or reason about it: ellipse centred at
`(lat, lon)`, rotated to `uncertainty_heading_deg`, semi-axis-along-heading =
`sigma_along_m`, semi-axis-perpendicular = `sigma_cross_m`.

### How the DSS solver typically maps these fields

| Decision input | Fields |
|---|---|
| Where is it (and how sure) | `lat`, `lon`, `sigma_along_m`, `sigma_cross_m`, `uncertainty_heading_deg`, `age_sec` |
| Can it still do the job | `capabilities`, `sensor_health`, `status`, `battery_pct` |
| Comms budget / can I task it | `link_quality`, `residual_bandwidth_kbps`, `expected_next_contact_sec`, `in_blackout` |
| Current commitment | `current_task`, `waypoint`, `velocity` |
| Threats & environment | `contacts`, `weather` (sea state degrades comms & UUV/USV motion) |

## Contact object

| Field | Type | Meaning |
|---|---|---|
| `id` | str | e.g. `TGT-01` |
| `lat`, `lon` | float | reported position |
| `speed_knots` | float | speed |
| `heading` | float | heading (°) |
| `ais` | bool | `false` = "dark" (no AIS transponder) |
| `behavior` | str | e.g. `hostile` |
| `detected_ts` | float | Unix seconds when first detected |

## Weather object

`{ wave_height_m, wave_period_s, wave_direction_deg, wind_speed_kn,
wind_direction_deg, air_temp_c, sea_state (0–9 Douglas), sea_state_label,
source, updated_ts }`, or `{ error, source }` if the fetch failed.

## AIS vessel object

`{ mmsi, name, lat, lon, heading, sog_knots, ts }`.

## Alert object

`{ type, vehicle, message, ts, sim_time_sec }`.

---

# REST API

| Method | Path | Description |
|---|---|---|
| GET | `/vehicles` | perceived vehicle estimates (same objects as the WS `vehicles`) |
| GET | `/contacts` | active threat contacts |
| GET | `/weather` | latest Open-Meteo conditions |
| GET | `/ais` | `{enabled, vessels}` — live AIS traffic |
| GET | `/estimate/{vehicle_id}` | position + cone for one vehicle: `{lat, lon, uncertainty_radius_m, sigma_along_m, sigma_cross_m, uncertainty_heading_deg, blackout_duration_sec, submerged, in_blackout}` |
| POST | `/scenario/{name}` | switch active scenario (`A` / `B`) and reset its timeline |
| POST | `/inject/{event_type}` | manually fire an event by name |
| POST | `/assign` | body `{vehicle_id, task, lat?, lon?}` → set task / waypoint |
| WS | `/ws` | broadcasts the full snapshot every 1 s |

`event_type` values: `sim_start`, `acoustic_loss`, `acoustic_regain`,
`threat_contact`, `sensor_failure`, `bingo_warning`.

# Scenarios

`ACTIVE_SCENARIO` in `main.py` selects which plays at startup (currently `B`).
Switch live with `POST /scenario/{A|B}`, or use the **Inject** buttons to fire
events on demand instead of waiting.

**SCENARIO_A — Underwater contact lost** (decision classes D4 + D1)

| T+ (sec) | Event | Effect |
|---|---|---|
| 0 | `sim_start` | simulation begins |
| 1080 | `acoustic_loss` (UUV-1) | UUV-1 submerges → dead-reckoning blackout, cone grows |
| 2580 | `acoustic_regain` (UUV-1) | UUV-1 surfaces → acoustic link re-established |

**SCENARIO_B — Threat appears + vehicle damaged** (decision classes D3 + D1 + D2)

| T+ (sec) | Event | Effect |
|---|---|---|
| 0 | `sim_start` | simulation begins |
| 1320 | `threat_contact` (TGT-01) | fast, AIS-dark hostile contact added |
| 1440 | `sensor_failure` (USV-2) | loses `passive_sonar`, marked `degraded` |

## Using the demo UI

- 6 vehicles on the map. **Triangle = UAV, boat = USV, circle = UUV.** Colour:
  green = nominal, yellow = degraded, red = lost_comms/bingo, grey = submerged.
- **Inject** buttons (top-left) fire scenario events manually.
- A silent/submerged vehicle draws a growing semi-transparent uncertainty
  region (from `/estimate/{id}`), labelled `In blackout — Xm Ys`.
- **Assign a waypoint:** click a vehicle → *Assign Waypoint* → click the map.
  A dashed line is drawn and the vehicle steers there. **Esc** cancels.
- **Grey chevrons** are real AIS traffic (when a key is configured).
- The right sidebar shows the fleet list, alert queue, and a live environment
  strip (sea state, waves, wind, temperature).

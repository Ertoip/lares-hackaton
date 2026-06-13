# Maritime DSS Prototype

A decision-support-system prototype for unmanned maritime operations in the
Strait of Messina. Three wired-together components:

1. **Synthetic telemetry generator** (`backend/simulator.py`) — 6 unmanned
   vehicles (2 UAV, 2 USV, 2 UUV) moving in real time, with battery drain,
   comms links, and dead-reckoning for submerged UUVs.
2. **Event injection timeline** (`backend/events.py`) — scenario events that
   fire on a clock and mutate vehicle state, plus manual injection.
3. **FastAPI server + WebSocket broadcast** (`backend/main.py`).
4. **React + Leaflet map UI** (`frontend/src/App.jsx`).

Live external data is layered on top of the simulation:

- **Open-Meteo** (`backend/weather.py`) — real marine + atmospheric
  conditions (wave height/period/direction, wind, air temp, Douglas sea
  state) for the Strait, shown in the sidebar environment strip. Key-less.
- **AISStream.io** (`backend/ais.py`) — real-world vessel traffic streamed
  over WebSocket and drawn in grey on the map. Requires a free API key.
- **Land/water masking** (`backend/geo.py`) — surface and subsurface
  vehicles (USV/UUV) are kept on the water; they never drive onto land.

```
.
├── backend/
│   ├── simulator.py     # vehicle state + physics + dead reckoning
│   ├── events.py        # SCENARIO_B timeline + scheduler
│   ├── geo.py           # land/water mask (keeps water vehicles at sea)
│   ├── weather.py       # Open-Meteo marine + weather poller
│   ├── ais.py           # AISStream.io live vessel-traffic client
│   ├── main.py          # FastAPI app, WebSocket, REST
│   └── requirements.txt
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx      # map + sidebar
        ├── main.jsx
        └── index.css
```

## Prerequisites

- Python 3.10+ (uses `X | None` type syntax)
- Node.js 18+

## 1. Start the backend (port 8000)

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload
```

### Optional: real AIS traffic

The map shows real nearby vessels (grey chevrons) when an
[AISStream.io](https://aisstream.io) API key is provided. Without it the
feature stays dormant — everything else works unchanged.

```bash
# Windows PowerShell:
$env:AISSTREAM_API_KEY = "your-key-here"
# macOS/Linux:
# export AISSTREAM_API_KEY=your-key-here
uvicorn main:app --reload
```

Backend is now at <http://localhost:8000>. Quick checks:

- `GET  http://localhost:8000/vehicles`
- `GET  http://localhost:8000/contacts`
- `GET  http://localhost:8000/estimate/UUV-1` (after acoustic loss)
- `POST http://localhost:8000/inject/threat_contact`
- WebSocket: `ws://localhost:8000/ws`

## 2. Start the frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

## Using the demo

- The map shows all 6 vehicles. **Triangle = UAV, boat = USV, circle = UUV.**
  Colour: green = nominal, yellow = degraded, red = lost_comms/bingo,
  grey = submerged.
- The **Inject** buttons (top-left of the map) manually fire scenario events.
  Otherwise `SCENARIO_B` plays out automatically on the sim clock (the first
  scripted event, acoustic loss on UUV-1, fires at T+1080s).
- When a UUV goes into **acoustic blackout**, the UI polls
  `/estimate/{id}` every 3s and draws a semi-transparent blue uncertainty
  circle that grows over time, labelled `In blackout — Xm Ys`.
- **Assign a waypoint / direction:** click a vehicle marker → *Assign
  Waypoint* (or click a vehicle row in the sidebar). The selected vehicle
  turns **cyan with a pulsing halo** so you can see which one you're
  commanding; then click a point on the map. A dashed line is drawn from the
  vehicle to the waypoint and it steers toward it. Press **Esc** to cancel.
  (Waypoints dropped on land for water vehicles are snapped to the nearest sea
  point.)
- **Grey chevrons** are real AIS vessel traffic (when a key is configured).
- The **right sidebar** shows the fleet list, an alert queue (max 3 cards,
  each with an Acknowledge button), and a live environment strip fed by
  Open-Meteo (sea state, waves, wind, temperature).

## Scenario timeline (`SCENARIO_B`)

| T+ (sec) | Event | Effect |
|---|---|---|
| 0 | `sim_start` | simulation begins |
| 1080 | `acoustic_loss` (UUV-1) | UUV-1 submerges → dead-reckoning blackout |
| 1320 | `threat_contact` (TGT-01) | hostile, AIS-dark contact added |
| 1440 | `sensor_failure` (USV-2) | loses `passive_sonar`, marked degraded |
| 1500 | `bingo_warning` (UAV-1) | battery → 22%, status bingo |

To watch the full timeline without waiting ~18 minutes, just use the manual
**Inject** buttons.

## REST API summary

| Method | Path | Description |
|---|---|---|
| GET | `/vehicles` | current state snapshot of all vehicles |
| GET | `/contacts` | active threat contacts |
| GET | `/weather` | latest Open-Meteo marine + atmospheric conditions |
| GET | `/ais` | `{enabled, vessels}` — live AIS traffic |
| GET | `/estimate/{vehicle_id}` | dead-reckoned position + uncertainty for a submerged UUV |
| POST | `/inject/{event_type}` | manually fire an event by name |
| POST | `/assign` | `{vehicle_id, task, lat, lon}` → update task / waypoint |
| WS | `/ws` | broadcasts `{vehicles, contacts, ais, weather, alerts, sim_time_sec}` every 1s |

`event_type` values: `acoustic_loss`, `threat_contact`, `sensor_failure`,
`bingo_warning`, `sim_start`.

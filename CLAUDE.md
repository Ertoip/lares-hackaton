# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A decision-support-system (DSS) prototype for unmanned maritime operations in the **Dover Strait**. A Python/FastAPI backend simulates the **operator's perceived picture** of a 6-vehicle fleet (2 UAV, 2 USV, 2 UUV) and streams it over a WebSocket; a React + Leaflet UI renders it. A separate teammate-owned DSS engine consumes the same stream to recommend actions.

`README.md` contains the full per-field **data contract** (the WebSocket/REST schema). Treat it as the source of truth for field names, units, and meaning — don't restate it; read it when touching the snapshot shape.

## Commands

Backend (port 8000), run from `backend/`:
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Frontend (port 5173), run from `frontend/`:
```bash
npm install
npm run dev        # vite dev server
npm run build      # production build — also the only "does it compile?" check
```

There is **no test suite and no linter configured.** `npm run build` is the de facto frontend validation gate; for backend changes, exercise the running server (`GET /vehicles`, `GET /estimate/UUV-1`, the `/ws` socket, and the `POST /inject/{event_type}` buttons in the UI).

Optional real AIS traffic: set `AISSTREAM_API_KEY` in the environment before launching the backend. Without it, `ais` is `[]` and the AIS client stays dormant — this is expected, not a bug.

## Architecture & non-obvious facts

**Perceived-only, single-state design.** This is the central idea. `perceived_sim.py` keeps no hidden ground truth — it simulates *only what the operator can know*: one best-estimate position per vehicle plus a growing **uncertainty cone**. The "real" motion lives in `_advance_fix()` and only materializes when a comms roll says a fix arrives; otherwise `_update_estimate()` dead-reckons forward and grows the uncertainty. The solver never sees a position the real system couldn't have known. Do not add a ground-truth channel.

- `backend/perceived_sim.py` — **the active simulator** (`PerceivedSimulator`). `backend/simulator.py` is the **legacy two-state** sim, kept only as a fallback and not wired into the running server. Don't edit `simulator.py` for live behavior.
- `backend/comms.py` — pure per-link physics (radio/satellite/acoustic differ sharply): drop probability, baseline σ, σ-growth rate, residual bandwidth. Sea state from `weather.py` feeds these.
- `backend/events.py` — `SCENARIO_A` / `SCENARIO_B` scripted timelines + the scheduler that fires them on the sim clock, and `apply_event()` which mutates sim state. Same events are addressable by name via `POST /inject/{event_type}`.
- `backend/main.py` — FastAPI app, the 0.25s sim/scheduler loop, the **1 Hz** WebSocket broadcast, and REST. `ACTIVE_SCENARIO` (currently `"B"`) selects the startup timeline; switch live with `POST /scenario/{A|B}`.
- `backend/geo.py`, `weather.py`, `ais.py` — land/water mask (keeps USV/UUV on water), Open-Meteo poller, AISStream.io client.

**The WebSocket snapshot is the single source of truth for the UI.** Every vehicle record already carries its dead-reckoned `lat`/`lon` *and* the full anisotropic uncertainty (`sigma_along_m`, `sigma_cross_m`, `uncertainty_heading_deg`, `sigma_m`, plus `stale`/`in_blackout`). `GET /estimate/{id}` returns the *same* numbers and is redundant for rendering — the frontend derives the position cone directly from the snapshot. Prefer the snapshot; don't reintroduce per-vehicle estimate polling.

**Uncertainty cone math.** When silent, uncertainty is an ellipse aligned to the last heading: along-track grows with *time*, cross-track grows with *distance × heading-drift* (capped at 45°), so it starts pointing forward and fans into a search cone. The frontend's `uncertaintyEllipse()` in `App.jsx` mirrors the backend model in `_update_estimate()` — keep the two consistent if you change one.

**The DSS bridge is a separate, optional process.** `backend/dss_bridge.py` adapts this sim's 1-snapshot-per-second broadcast into the teammate DSS's per-vehicle client-push schema (port 8001, documented in `README_dss.md`). It imports nothing from the backend and edits no existing file — it's deliberately backtrack-safe (stop the process or `git rm` it to fully undo). Don't couple the sim to it.

## Frontend specifics

Single-component app: `frontend/src/App.jsx` (map + sidebar) with styling in `src/index.css`. State arrives via one WebSocket; the only writes back to the backend are `POST /assign` (waypoint, via click-a-vehicle → Assign Waypoint → click map) and `POST /inject/*` (demo buttons). Acknowledging an alert is a **client-only placeholder** for a future DSS-driven operator action — acknowledged alerts are kept in component state (they outlive the backend's 10-item alert cap).

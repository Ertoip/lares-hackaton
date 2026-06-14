# DSS Integration — Sending and Receiving Data

This is the **authoritative, end-to-end reference** for how this perceived-sim
exchanges data with the teammate's Decision Support System (DSS). It documents
**both directions** in full:

- **Sending → DSS** (the *forward path*): how the sim's once-per-second world
  picture is translated and pushed into the DSS as strict per-vehicle messages.
- **Receiving ← DSS** (the *reverse path*): how the DSS's decisions come back as
  commands and get executed (or queued) in the sim.

> If you only want the schema of one message type, jump to its section. If you
> are wiring the two systems together for the first time, read
> [Architecture](#architecture) then [Run order](#run-order) first.

Related, narrower docs this file supersedes/consolidates:
`backend/DSS_BRIDGE.md` (forward, teammate-facing), `README_dss_commands.md`
(reverse contract), `README_dss.md` (the teammate's own backend), and the
`# Data contract` section of `README.md` (the sim snapshot this all starts from).

---

## Architecture

Three processes. **Two independent servers** that know nothing about each
other's schema, plus **one thin translator** (the bridge) sitting at the edge.

```
                              FORWARD  (sending → DSS)
  ┌────────────────┐  ws /ws     ┌────────────────┐  ws /dss/ws/vehicles  ┌──────────────┐
  │  perceived-sim │ ──────────▶ │  dss_bridge.py │ ───────────────────▶  │  teammate's  │
  │  (port 8000)   │  1 fat       │  (translator)  │  strict per-vehicle    │  DSS (8001)  │
  │                │  snapshot/s  │                │  heartbeat/telemetry/  │              │
  │                │              │                │  event/link_state      │              │
  │                │ ◀────────── │                │ ◀───────────────────  │              │
  │  POST /command │  HTTP POST   │  run_reverse() │  ws /dss/ws/commands   │              │
  └────────────────┘  ack         └────────────────┘  command frames        └──────────────┘
                              REVERSE  (receiving ← DSS)
```

Key properties:

- **The sim is the source of truth.** Everything originates from one JSON
  snapshot the sim broadcasts every second on `ws://localhost:8000/ws`
  (full schema in [`README.md` → Data contract](README.md)). The bridge never
  invents data; it only reshapes the snapshot into the DSS's message schema.
- **The DSS uses `extra="forbid"`.** Its Pydantic models reject any field they
  don't recognise, so the bridge emits **only** documented fields. Getting a
  field name or enum value wrong is a hard rejection, not a silent drop.
- **The bridge is backtrack-safe.** `backend/dss_bridge.py` imports nothing from
  the backend and edits no existing file. To remove the whole integration: stop
  the bridge process, or `git rm backend/dss_bridge.py`. The sim, the UI, and
  the `/ws` contract are completely unaffected.
- **Forward and reverse are independent.** They run as two `asyncio` tasks
  (`run_forward()` / `run_reverse()`); either socket dropping retries on its own
  (2 s backoff) without stopping the other.

### The two schemas, side by side

| | This sim (`/ws`) | Teammate DSS (`/dss/ws/vehicles`) |
|---|---|---|
| Role | **server**, broadcasts | **server**, receives client pushes |
| Shape | one fat snapshot/sec (all vehicles + contacts + alerts + weather + AIS) | many small, strictly-validated **per-vehicle** messages |
| Vehicle ids | `UAV-1/2`, `USV-1/2`, `UUV-1/2`, `CSV MERIDIAN` | `air_1/2`, `surface_1/2`, `sub_1/2`, `mothership_1` |
| Domain field | `type` (`UAV`/`USV`/`UUV`) | `domain` (`air`/`surface`/`subsurface`) |
| Message types | one (the snapshot) | four (`heartbeat`, `telemetry`, `event`, `link_state`) |
| Validation | none (trusted internal) | strict, `extra="forbid"` |

The bridge exists precisely because these do not line up.

---

## Run order

```bash
# 1. our sim                      (port 8000)
cd backend && uvicorn main:app --reload

# 2. teammate's DSS               (port 8001)
uvicorn dss_backend.main:app --reload --port 8001

# 3. the bridge (both directions)
python backend/dss_bridge.py
```

Every endpoint is environment-overridable, so non-default ports/hosts work
without code changes:

| Env var | Default | What it points at |
|---|---|---|
| `SIM_WS_URL` | `ws://localhost:8000/ws` | sim snapshot stream (read) |
| `DSS_WS_URL` | `ws://localhost:8001/dss/ws/vehicles` | DSS telemetry intake (write) |
| `DSS_CMD_WS_URL` | `ws://localhost:8001/dss/ws/commands` | DSS command output (read) |
| `SIM_CMD_URL` | `http://localhost:8000/command` | sim command intake (write) |

```bash
SIM_WS_URL=ws://localhost:8000/ws \
DSS_WS_URL=ws://host:8001/dss/ws/vehicles \
python backend/dss_bridge.py
```

The bridge prints `[DSS rejected] <error>: <details>` for any ack where
`ok=false` — that is your first stop when a schema disagreement happens.

---

## Vehicle identity mapping

Every translation in **both** directions hinges on this table. Domain and
heartbeat interval are derived from the sim id's type prefix.

| Sim id | DSS `vehicle_id` | `domain` | `communication_mode` | `expected_interval_ms` |
|---|---|---|---|---|
| `UAV-1` / `UAV-2` | `air_1` / `air_2` | `air` | `radio` | `1000` |
| `USV-1` / `USV-2` | `surface_1` / `surface_2` | `surface` | `satellite` | `3000` |
| `UUV-1` / `UUV-2` | `sub_1` / `sub_2` | `subsurface` | `acoustic` | `30000` |
| `CSV MERIDIAN` | `mothership_1` | `surface` | — | — |

(`DSS_ID` / `SIM_ID` / `DOMAIN_BY_TYPE` / `INTERVAL_MS` in `dss_bridge.py`.)
`communication_mode` is actually taken per-snapshot from the vehicle's live
`link_type` (`radio`/`satellite`/`acoustic`), defaulting to `radio`; the table
shows the steady-state value.

---

# Part 1 — Sending data TO the DSS (forward path)

`run_forward()` opens both sockets, reads each sim snapshot, and fans it out
into DSS messages via `process_snapshot()`. One snapshot can produce many
messages (one or more per vehicle, plus contact/alert events).

## What maps to what

| Sim snapshot element | → DSS message | Cadence |
|---|---|---|
| vehicle, in contact | `heartbeat` | at the domain's `expected_interval_ms` |
| vehicle, in contact | `telemetry` | every snapshot (~1 Hz) |
| vehicle, submerged / in blackout | `link_state` (`expected_blackout`) | **once** on entry; heartbeat + telemetry **withheld** |
| vehicle, regained contact | `link_state` (`online`) | once on exit |
| `mothership` | `telemetry` (as `mothership_1`) | every snapshot |
| threat `contact` | `event` (`unknown_contact`) | once per contact id |
| `alert` | `event` (alert kind) | once per synthesised event id |

### Why blackout withholds telemetry (the honesty rule)

This is the central design point and mirrors the sim's perceived-only thesis.
While a vehicle is silent (`submerged` or `in_blackout`), the bridge sends **no
heartbeat and no telemetry** — there is no real channel carrying that data, so
faking liveness would lie to the DSS. Instead it sends a single
`link_state(expected_blackout)` with a contact window. That is exactly what lets
the DSS's subsurface logic transition `expected_blackout → late_contact` rather
than show false freshness. The window length defaults to
`BLACKOUT_WINDOW_SEC = 1500 s` (≈ Scenario A's acoustic blackout); on regaining
contact a single `link_state(online)` clears it.

Blackout is detected by `_is_blackout(p) = submerged OR in_blackout`. Entry and
exit are edge-triggered against `BridgeState.in_blackout[dss_id]` so each
link_state is sent exactly once.

## Message schemas (exact fields the bridge emits)

All timestamps are ISO 8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`, from the server clock.

### heartbeat

Proves the vehicle is reachable *right now*. Sent at the domain interval, not
every snapshot — a per-vehicle monotonic timer (`BridgeState.last_hb`) gates it.

```json
{
  "message_type": "heartbeat",
  "vehicle_id": "air_1",
  "timestamp": "2026-06-14T12:00:00Z",
  "sequence": 184,
  "domain": "air",
  "communication_mode": "radio",
  "expected_interval_ms": 1000
}
```

| Field | Source / rule |
|---|---|
| `sequence` | per-vehicle counter starting at 0 (`BridgeState.next_seq`) |
| `communication_mode` | from `link_type`; `radio`/`satellite`/`acoustic` |
| `expected_interval_ms` | fixed by domain (`air=1000`, `surface=3000`, `subsurface=30000`) — the DSS **rejects** a mismatch |

### telemetry

Full physical + mission state. Sent every snapshot while in contact.

```json
{
  "message_type": "telemetry",
  "vehicle_id": "air_1",
  "timestamp": "2026-06-14T12:00:01Z",
  "domain": "air",
  "status": "active",
  "lat": 51.041230,
  "lon": 1.553400,
  "position": { "lat": 51.041230, "lon": 1.553400, "alt": 120.0, "depth": null },
  "velocity": { "speed_mps": 7.305, "heading_deg": 82.0 },
  "battery": { "percentage": 76.0, "bingo_threshold": 15 },
  "sensors": { "camera": "ok", "radar": "ok", "sonar": "unavailable" },
  "capabilities": { "visual_isr": true, "radar_scan": true, "sonar_scan": false, "relay_comms": false },
  "current_task_id": "ASW screen"
}
```

Field-by-field translation (`build_telemetry` + helpers):

| DSS field | Derived from sim field(s) | Rule |
|---|---|---|
| `status` | `status` | `nominal`→`active`, `degraded`→`active` (health shown per-sensor), `bingo`→`returning`, `lost_comms`→`offline`. Never emits `idle`/`standby`/`fault`. |
| `lat` / `lon` | `lat` / `lon` | rounded to 6 dp; the best-estimate (dead-reckoned) position |
| `position.alt` | `z_m` | air only (`alt = z_m`); else `null` |
| `position.depth` | `z_m` | subsurface only (`depth = max(0, -z_m)`, DSS requires ≥ 0); else `null` |
| `velocity.speed_mps` | `speed_knots` | × `0.514444` (knots→m/s), 3 dp |
| `velocity.heading_deg` | `heading` | normalised to `0 ≤ h < 360` |
| `battery.percentage` | `battery_pct` | clamped 0–100 |
| `battery.bingo_threshold` | — | **hardcoded 15** (`BINGO_THRESHOLD_PCT`) |
| `sensors.{camera,radar,sonar}` | `capabilities` + `sensor_health` | see below |
| `capabilities.*` | `capabilities` | booleans (see below) |
| `current_task_id` | `current_task` | a free-text string (e.g. `"ASW screen"`), not a slug |

**Sensor health mapping** (`_sensors`). The DSS has three fixed slots; each maps
from one or more sim capability strings (`SENSOR_SOURCES`):

| DSS slot | Sim capabilities feeding it |
|---|---|
| `camera` | `visual_ISR`, `thermal_ISR` |
| `radar` | `surface_radar` |
| `sonar` | `active_sonar`, `passive_sonar` |

Per slot: present **and** healthy (`sensor_health > 0`) → `ok`; previously
present but failed (`sensor_health == 0`) → `fault`; never fitted →
`unavailable`. The sim removes a failed capability from `capabilities` but keeps
it in `sensor_health` at `0.0`, which is exactly what lets the bridge tell
`fault` apart from `unavailable`. (The bridge never emits `degraded`.)

**Capability booleans** (`_capabilities`):

| DSS flag | True when sim `capabilities` contains |
|---|---|
| `visual_isr` | `visual_ISR` or `thermal_ISR` |
| `radar_scan` | `surface_radar` |
| `sonar_scan` | `active_sonar` or `passive_sonar` |
| `relay_comms` | `comms_relay` |

### telemetry — mothership

The crewed command vessel is a special case (`build_mothership_telemetry`):
always `surface`, always `status: "active"`, always a **known** position (no
uncertainty). It carries `battery: null`, `sensors: {}`, and only
`relay_comms: true` among capabilities. Sent as `mothership_1` every snapshot
from the snapshot's top-level `mothership` object.

### link_state

Sent only on blackout **entry** and **exit** (see the honesty rule above).

```json
{
  "message_type": "link_state",
  "vehicle_id": "sub_1",
  "timestamp": "2026-06-14T12:05:00Z",
  "domain": "subsurface",
  "communication_mode": "acoustic",
  "status": "expected_blackout",
  "last_contact_at": "2026-06-14T12:00:00Z",
  "expected_next_contact_window": { "start": "2026-06-14T12:25:00Z", "end": "2026-06-14T12:30:00Z" }
}
```

| Field | Rule |
|---|---|
| `status` | `expected_blackout` on entry, `online` on exit |
| `last_contact_at` | the vehicle's `last_contact_ts` |
| `expected_next_contact_window` | `{start, end}` for blackout (`end = now + 1500 s`, `start` 5 min before `end`); `null` for `online` |

### event — threat contacts

Each threat `contact` in the snapshot becomes one `unknown_contact` event,
emitted once per contact id (`BridgeState.sent_contacts`).

```json
{
  "message_type": "event",
  "event_id": "TGT-01",
  "timestamp": "2026-06-14T12:22:00Z",
  "vehicle_id": "surface_1",
  "domain": "surface",
  "event_kind": "unknown_contact",
  "severity": "critical",
  "position": { "lat": 51.06, "lon": 1.65, "alt": null, "depth": null },
  "description": "Dark contact TGT-01 — hostile behaviour, 28 kn",
  "metadata": { "ais": "off", "speed_knots": 28, "heading": 210, "behavior": "hostile" }
}
```

| Field | Rule |
|---|---|
| `severity` | from behaviour: `hostile`→`critical`, `suspicious`→`high`, `cooperative`→`low`, else `medium` |
| `vehicle_id` / `domain` | **the nearest non-blackout fleet vehicle** — the DSS schema requires a reporting vehicle and our contacts aren't tied to one, so the detection is attributed to whoever is closest and currently heard (`_nearest_reporter`). Falls back to `surface_1` if none qualifies. |
| `description` | `"Dark"` vs `"AIS"` prefix from the `ais` flag, plus behaviour + speed |
| `metadata` | `{ais: on/off, speed_knots, heading, behavior}` |

### event — alerts

Each snapshot `alert` tied to a fleet vehicle becomes one event, emitted once
per synthesised id (`BridgeState.sent_alerts`). Alerts with no fleet vehicle
(e.g. `sim_start`) are skipped.

| Field | Rule |
|---|---|
| `event_kind` | the alert `type` (e.g. `sensor_failure`, `acoustic_loss`, `bingo_warning`) |
| `event_id` | synthesised: `alert_<type>_<vehicle>_<sim_time_sec>` |
| `severity` | `sensor_failure`→`high`, `acoustic_loss`→`medium`, `bingo_warning`→`high`, else `medium` |
| `vehicle_id` / `domain` | mapped from the alert's vehicle |
| `position` | the reporting vehicle's last position, or `null` |

## Acks the bridge reads back

The DSS replies to every frame on the same socket. The bridge drains these in
`_drain_acks()` and logs only failures:

```
[DSS rejected] Validation failed: [ ... pydantic details ... ]
```

A success ack (`{"ok": true, ...}`) is consumed silently. Use the rejection log
to find any field/enum the DSS disagrees with, then adjust the corresponding
builder in `dss_bridge.py` — no change is needed on the DSS side.

---

# Part 2 — Receiving data FROM the DSS (reverse path)

The DSS *decides*; the sim *executes*. The reverse path closes the loop: the DSS
pushes commands, the bridge translates and POSTs them to the sim, and the sim
applies them — respecting the comms model, so an out-of-contact vehicle's order
**queues** until it can actually be delivered.

## How it flows

```
DSS decides ──ws push──▶ /dss/ws/commands ──▶ dss_bridge.run_reverse()
                                                  │ translate id + action
                                                  ▼
                                      POST http://localhost:8000/command
                                                  │
                                                  ▼ ack {executed | pending | rejected}
                                        sim executes the order, or queues it
```

`run_reverse()` connects to the DSS command socket as a client, reads one JSON
command per frame, calls `translate_command()`, and POSTs the result to the
sim's `/command` (off the event loop via `asyncio.to_thread`). It logs each ack.

## What the DSS must emit

A WebSocket the bridge can read command frames from (override with
`DSS_CMD_WS_URL`):

```
WS  ws://localhost:8001/dss/ws/commands
```

One JSON command per frame:

```json
{
  "command_id": "cmd_0007",
  "vehicle_id": "sub_1",
  "action": "reroute",
  "params": { "lat": 51.05, "lon": 1.55, "task": "Investigate contact 12" }
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `command_id` | string | recommended | DSS's id for the order; echoed in the ack. Auto-generated if omitted. |
| `vehicle_id` | string | yes | **DSS** id (`air_1/2`, `surface_1/2`, `sub_1/2`); the bridge maps it to the sim id (`sub_1 → UUV-1` via `SIM_ID`). |
| `action` | string | yes | one of the canonical actions below |
| `params` | object | per-action | action arguments |
| `issued_by` | string | no | free label, defaults to `"dss"` |
| `ts` | string | no | DSS timestamp, carried through for traceability |

## Translation in the bridge

`translate_command()` does two lookups and drops anything it can't resolve
(unknown ids/actions are **not** guessed):

1. `vehicle_id` → sim id via `SIM_ID` (`sub_1 → UUV-1`).
2. `action` → sim action via `DSS_ACTION_MAP`.

`DSS_ACTION_MAP` accepts the canonical action names **plus** a set of
**provisional aliases** (`goto`→`reroute`, `dive`→`set_depth`,
`return_home`→`rtb`, `loiter`→`hold`, …) kept only so integration isn't blocked
on exact naming. **These aliases are not the contract.** Once the DSS's real
action strings are confirmed, prune `DSS_ACTION_MAP` to exactly those (ideally
the canonical names, making the map an identity) and delete the rest.

The translated message is the sim's `CommandRequest`:
`{command_id, vehicle_id (sim id), action, params, issued_by, ts}`.

## The sim's command contract (`POST /command`)

Defined in `backend/commands.py` (`CommandRequest` + `ACTIONS`). This is the
canonical contract — the DSS should target these `action` names verbatim. They
map one-to-one onto execution primitives already on `PerceivedSimulator`.

| `action` | `params` | Effect in sim |
|---|---|---|
| `reroute` | `lat`, `lon`, `task?` | move to waypoint (keeps current task unless `task` given) |
| `reassign_task` | `task`, `lat?`, `lon?` | new task label, optional new waypoint |
| `set_depth` | `depth_m` | UUV/USV depth (m below surface; deeper = worse acoustic link, marks submerged) |
| `set_altitude` | `altitude_m` | UAV altitude (m above surface) |
| `surface` | — | bring a UUV to the surface (restores comms) |
| `submerge` | `depth_m?` | submerge a UUV (optional target depth) |
| `hold` | — | loiter in place (speed 0) until `resume` |
| `resume` | — | resume from a hold |
| `rtb` | — | return to the mothership; auto-docks + recharges on arrival |
| `abort` | — | drop current task, loiter, flag `awaiting_tasking` |
| `set_status` | `status` | override the reported status string |

Lat/lon are decimal degrees within the Dover Strait box
(lat `50.85–51.25`, lon `0.90–2.20`). Water vehicles sent a land point are
snapped to water automatically.

**Param validation** (`_validate_params`) rejects, with a reason, a `reroute`
missing `lat`/`lon`, a `reassign_task` missing `task`, a `set_depth`/
`set_altitude` without a numeric depth/altitude, or a `set_status` missing
`status`.

## The ack (`POST /command` reply)

Synchronous. Echoes the command and reports outcome:

```json
{
  "command_id": "cmd_0007",
  "vehicle_id": "UUV-1",
  "action": "reroute",
  "status": "pending",
  "reason": "vehicle out of contact; queued until next contact window",
  "expected_next_contact_sec": 12.0,
  "ts": 1750000000.0
}
```

| `status` | Meaning |
|---|---|
| `executed` | vehicle was in contact; order applied immediately |
| `pending` | vehicle is **out of contact** (submerged / stale fix); the order is **queued** and fires the instant it is next reachable. `expected_next_contact_sec` is a hint. |
| `rejected` | unknown vehicle/action or missing required params (`reason` explains) |

### Why `pending` exists — comms-aware delivery

A command is a **downlink message**: it can only reach a vehicle the operator is
currently in contact with. A submerged UUV in acoustic blackout cannot receive
*any* order — **including `surface`** — because there is no channel to carry it.
So the sim queues it and delivers it the instant the vehicle is next heard from.
This applies the same perceived-only honesty the telemetry side uses: the sim
never pretends a message reached a vehicle it couldn't talk to.

Mechanics (`CommandDispatcher` in `commands.py`):

- **Reachability** = `not p["stale"]`. `stale` already encodes the comms physics
  (a submerged UUV's acoustic link drops ~98% of fixes, so it's stale almost
  always; the rare fix that slips through is a genuine two-way contact window).
  The dispatcher deliberately does **not** also gate on `submerged` — doing so
  would double-count the physics and make a commanded-down UUV permanently
  unreachable (it could never receive the `surface` order to come back).
- **Queue + drain.** An unreachable target's command is appended to a per-vehicle
  FIFO (`queue[vid]`). `main.py` calls `dispatcher.step()` once per sim tick
  (and after fast-forwards); `step()` drains every queued command for any vehicle
  that has regained contact, re-acking each as `executed` ("delivered at next
  contact window").

The DSS should treat `pending` as "accepted, not yet acted on" and watch
telemetry to confirm execution.

## Closing the loop (dock → re-tasking)

When a returning vehicle docks and recharges, the sim sets its task to a
`Awaiting tasking`-style string and emits an `awaiting_tasking` (or
`vehicle_docked`) alert. The **forward** bridge already forwards alerts as DSS
`event`s, so the DSS sees an event for an idle, available vehicle — the cue to
issue a `reassign_task` command back. That full round trip —
**dock → event → DSS decision → command → execution** — is the closed loop.

---

## Testing without the full DSS

The sim side is fully exercisable on its own — no DSS or bridge required.

**Forward (sending):** start the sim and read the raw snapshot the bridge
consumes:

```bash
curl http://localhost:8000/vehicles          # the per-vehicle objects
# or connect a ws client to ws://localhost:8000/ws for the full snapshot
```

**Reverse (receiving):** POST a command straight to the sim and inspect the
queue:

```bash
# reachable vehicle -> executed
curl -X POST localhost:8000/command -H "content-type: application/json" \
  -d '{"command_id":"c1","vehicle_id":"UAV-1","action":"reroute","params":{"lat":51.05,"lon":1.55,"task":"Investigate"}}'

# inspect recent acks + how many commands are queued per vehicle
curl localhost:8000/commands
```

**Bridge translation dry-run:** point `DSS_CMD_WS_URL` at a tiny local
websocket server that sends one command frame in the DSS shape; the bridge
translates it and POSTs to the sim, so you can verify the id/action mapping
before the real DSS command channel exists.

---

## File map

| File | Role |
|---|---|
| `backend/dss_bridge.py` | the translator — both `run_forward()` and `run_reverse()` |
| `backend/commands.py` | sim-side command contract + `CommandDispatcher` (queue/execute) |
| `backend/main.py` | exposes `POST /command`, `GET /commands`, the `/ws` broadcast |
| `backend/perceived_sim.py` | the sim; owns every execution primitive a command routes to |
| `README.md` (Data contract) | the snapshot schema the forward path starts from |
| `README_dss.md` | the teammate's DSS intake backend (their `/dss/ws/vehicles` schema) |
| `backend/DSS_BRIDGE.md` | teammate-facing forward-path field checklist |
| `README_dss_commands.md` | the reverse-path command contract, DSS-facing |

## Open integration questions (confirm with the DSS owner)

These were not yet locked at the time of writing:

1. Do the DSS enums for telemetry `status` (`active/idle/standby/fault/returning/offline`)
   and `sensors` (`ok/degraded/fault/unavailable`) exactly match? The bridge
   currently never emits `idle/standby/fault` (status) or `degraded` (sensors).
2. Is attributing threat `event`s to the **nearest** vehicle acceptable, or
   should it always be a fixed synthetic reporter?
3. Is a free-text `current_task_id` OK, or is a constrained id token required?
4. `bingo_threshold` — fixed 15 for all, or per-vehicle from the mission plan?
5. Which `action` strings does the DSS actually emit? Confirm so the provisional
   aliases in `DSS_ACTION_MAP` can be pruned to the real vocabulary.

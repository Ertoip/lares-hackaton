# DSS Bridge — integration contract

`dss_bridge.py` streams our perceived-sim picture into your DSS engine. This
doc is for **you (DSS side)** to verify that what the bridge emits matches your
Pydantic models. If your `extra="forbid"` schema rejects anything, the field
lists below are where to look — tell me which field differs and I'll adjust the
bridge, no changes needed on your end.

## Architecture

Two independent servers + a thin translator. Nothing in our simulator knows
your schema; the bridge is an adapter at the edge.

```
our sim (8000)  --ws /ws-->  dss_bridge.py  --ws /dss/ws/vehicles-->  your DSS (8001)
   broadcasts 1 fat            translates +                  receives strict
   snapshot / second           fans out per vehicle          per-vehicle messages
```

The bridge connects to **both** sockets as a client, auto-reconnects if either
drops, and prints `[DSS rejected] ...` for any ack where `ok=false`.

## Run order

```bash
uvicorn main:app --reload                            # our sim   (8000)
uvicorn dss_backend.main:app --reload --port 8001    # your DSS  (8001)
python dss_bridge.py                                 # the bridge
```

Override endpoints if your ports differ:

```bash
SIM_WS_URL=ws://localhost:8000/ws \
DSS_WS_URL=ws://localhost:8001/dss/ws/vehicles \
python dss_bridge.py
```

## Vehicle identity mapping

Our fleet ids → your six fixed vehicles. Domain and heartbeat interval are
derived from the type prefix.

| Our id | `vehicle_id` | `domain` | `communication_mode` | `expected_interval_ms` |
|---|---|---|---|---|
| `UAV-1` / `UAV-2` | `air_1` / `air_2` | `air` | `radio` | `1000` |
| `USV-1` / `USV-2` | `surface_1` / `surface_2` | `surface` | `satellite` | `3000` |
| `UUV-1` / `UUV-2` | `sub_1` / `sub_2` | `subsurface` | `acoustic` | `30000` |

## What the bridge emits, and when

| Our state | → your message | Cadence |
|---|---|---|
| vehicle, normal | `heartbeat` | at the domain `expected_interval_ms` |
| vehicle, normal | `telemetry` | every snapshot (~1 Hz) |
| vehicle, submerged / blackout | `link_state` (`expected_blackout`) | once on entry; telemetry + heartbeat **withheld** |
| vehicle, regained contact | `link_state` (`online`) | once on exit |
| threat `contact` | `event` (`unknown_contact`) | once per contact id |
| notable `alert` | `event` | once per alert |

> **Blackout is modelled honestly:** while a vehicle is silent we send *no*
> heartbeat or telemetry — only the `expected_blackout` link_state with a
> contact window. This is what lets your subsurface logic show
> `expected_blackout → late_contact` instead of false liveness. Window length
> defaults to 1500 s (`BLACKOUT_WINDOW_SEC` in the bridge), ~matching our
> Scenario A acoustic blackout.

## Exact field contents (verify against your models)

### heartbeat
`message_type, vehicle_id, timestamp, sequence, domain, communication_mode, expected_interval_ms`
- `sequence` — per-vehicle counter starting at 0
- `timestamp` — ISO 8601 `...Z`, UTC server clock

### telemetry
`message_type, vehicle_id, timestamp, domain, status, position, velocity, battery, sensors, capabilities, current_task_id`

- `status` ∈ `active | returning | offline` — mapped from our status:
  `nominal`→`active`, `degraded`→`active` (degradation shows in `sensors`),
  `bingo`→`returning`, `lost_comms`→`offline`.
  *(We never emit `idle | standby | fault` — flag if you need those.)*
- `position` = `{lat, lon, alt, depth}`; `alt` set for air only, `depth` (≥0)
  for subsurface only, the unused one is `null`.
- `velocity` = `{speed_mps, heading_deg}` (`0 ≤ heading_deg < 360`).
- `battery` = `{percentage, bingo_threshold}`; **`bingo_threshold` hardcoded 15**.
- `sensors` = `{camera, radar, sonar}`, each ∈ `ok | fault | unavailable`.
  Derived from our capability list + health: present & healthy → `ok`;
  previously present, now failed → `fault`; never fitted → `unavailable`.
  *(We never emit `degraded` — flag if you want a partial-health value.)*
- `capabilities` = `{visual_isr, radar_scan, sonar_scan, relay_comms}` booleans.
- `current_task_id` — a human-readable task string (e.g. `"ASW screen"`), not a
  slug. Flag if you need an id-shaped token.

### link_state
`message_type, vehicle_id, timestamp, domain, communication_mode, status, last_contact_at, expected_next_contact_window`
- `status` ∈ `expected_blackout | online`.
- `expected_next_contact_window` = `{start, end}` for `expected_blackout`,
  `null` for `online`.

### event (threat contacts)
`message_type, event_id, timestamp, vehicle_id, domain, event_kind, severity, position, description, metadata`
- `event_kind` = `unknown_contact`.
- `severity` from behaviour: `hostile`→`critical`, `suspicious`→`high`,
  `cooperative`→`low`, else `medium`.
- **`vehicle_id` / `domain` = the nearest non-blackout fleet vehicle** — your
  schema requires a reporting vehicle, and our contacts aren't tied to one, so
  we attribute the detection to whoever is closest and currently heard. Falls
  back to `surface_1` if none qualifies.
- `metadata` = `{ais, speed_knots, heading, behavior}`.

### event (alerts)
Same envelope. `event_kind` is the alert type (e.g. `sensor_failure`,
`acoustic_loss`, `bingo_warning`), `event_id` synthesised as
`alert_<type>_<vehicle>_<sim_time_sec>`, `vehicle_id`/`domain` mapped from the
alert's vehicle. Alerts without a fleet vehicle (e.g. `sim_start`) are skipped.

## Open questions for you

1. Do your enums for telemetry `status` and `sensors` exactly match the README?
   If you accept only `active/idle/standby/fault/returning/offline` and
   `ok/degraded/fault/unavailable`, we're fine — but confirm.
2. Is attributing threat `event`s to the nearest vehicle acceptable, or do you
   want a synthetic reporter (e.g. always `surface_1`)?
3. Is a free-text `current_task_id` OK, or do you need a constrained id?
4. `bingo_threshold` — fixed 15 for all, or per-vehicle from your mission plan?

Answer these and the bridge mapping is locked.

# DSS → Sim Command Channel (reverse path)

This documents the **command-output channel** the DSS needs to expose so its
decisions get executed in the perceived-sim. It is the return leg of the existing
telemetry stream (forward path described in `README_dss.md` + `backend/dss_bridge.py`).

> **Status:** the sim side is built and live. The DSS does *not* emit commands yet
> (`README_dss.md`: *"It does not provide command output APIs yet"*). This file is
> the contract for when you add that output. Nothing else needs to change on the
> sim side once you emit this shape.

## How it flows

```
DSS decides  ──ws push──▶  /dss/ws/commands  ──▶  dss_bridge (reverse path)
                                                      │ translate id + action
                                                      ▼
                                          POST http://localhost:8000/command
                                                      │
                                                      ▼  ack {executed|pending|rejected}
                                            sim executes (or queues) the order
```

The bridge (`backend/dss_bridge.py`, `run_reverse()`) connects to your command
socket, translates each message into the sim's `/command` contract, POSTs it, and
logs the ack. You only have to **emit the message below**; the bridge does the rest.

## What the DSS must expose

A WebSocket the bridge can connect to as a client and read command frames from:

```
WS  ws://localhost:8001/dss/ws/commands
```

(Override on the bridge with `DSS_CMD_WS_URL` if your path/port differs.)

Push **one JSON command per frame**:

```json
{
  "command_id": "cmd_0007",
  "vehicle_id": "sub_1",
  "action": "reroute",
  "params": { "lat": 51.05, "lon": 1.55, "task": "Investigate contact 12" }
}
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `command_id` | string | recommended | Your id for the order; echoed back in the ack. Auto-generated if omitted. |
| `vehicle_id` | string | yes | DSS vehicle id: `air_1/2`, `surface_1/2`, `sub_1/2`. The bridge maps it to the sim id (`sub_1 → UUV-1`). |
| `action` | string | yes | One of the canonical actions below. |
| `params` | object | per-action | Action arguments (see table). |
| `issued_by` | string | no | Free label, defaults to `"dss"`. |
| `ts` | string | no | Your timestamp; carried through for traceability. |

## Actions and params (canonical contract)

These names are the contract — **please emit them verbatim.** They mirror
`commands.py.ACTIONS` one-to-one.

| `action` | `params` | Effect in sim |
| --- | --- | --- |
| `reroute` | `lat`, `lon`, `task?` | Move to a waypoint (keeps task unless `task` given). |
| `reassign_task` | `task`, `lat?`, `lon?` | New task label, optional new waypoint. |
| `set_depth` | `depth_m` | UUV/USV depth (metres below surface; deeper = worse acoustic link, marks submerged). |
| `set_altitude` | `altitude_m` | UAV altitude (metres above surface). |
| `surface` | — | Bring a UUV to the surface (restores comms). |
| `submerge` | `depth_m?` | Submerge a UUV (optional target depth). |
| `hold` | — | Loiter in place (speed 0) until `resume`. |
| `resume` | — | Resume from a hold. |
| `rtb` | — | Return to the mothership; auto-docks + recharges on arrival. |
| `abort` | — | Drop current task, loiter, flag awaiting tasking. |
| `set_status` | `status` | Override the reported status string. |

Lat/lon are decimal degrees (Dover Strait box: lat 50.85–51.25, lon 0.90–2.20).
Water vehicles are snapped to water automatically if a land point is sent.

> **On synonyms / redundancy.** The bridge currently also accepts a few provisional
> aliases (`goto`→`reroute`, `dive`→`set_depth`, `return_home`→`rtb`, …) purely so we
> aren't blocked on exact naming before integration. They are **not** part of the
> contract. Once we connect and confirm the action strings the DSS actually emits,
> we prune `DSS_ACTION_MAP` in `dss_bridge.py` to exactly those — ideally the
> canonical names, leaving the map an identity we can delete. **Decide together which
> names you emit and we keep only those; the rest get removed.**

## The ack (what the bridge gets back, and logs)

`POST /command` replies synchronously:

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
| --- | --- |
| `executed` | The vehicle was in contact; order applied immediately. |
| `pending` | The vehicle is **out of contact** (submerged / stale fix). The order is **queued** and fires automatically the moment it is next reachable. `expected_next_contact_sec` is a hint. |
| `rejected` | Unknown vehicle/action or missing required params (`reason` explains). |

### Why `pending` exists (important)

A command is a **downlink message** — it can only reach a vehicle the operator is
currently in contact with. A submerged UUV in acoustic blackout cannot receive *any*
order, **including `surface`**; there is no channel to carry it. So the sim queues it
and delivers it the instant the vehicle is next heard from (its next contact window).
This is the same perceived-only honesty the telemetry side uses: we never pretend a
message reached a vehicle we couldn't talk to. Design the DSS to treat `pending` as
"accepted, not yet acted on" and watch telemetry to confirm execution.

## Closing the loop (dock → re-tasking)

When a returning vehicle docks and recharges, the sim sets its task to
`"Awaiting tasking"` and emits an `awaiting_tasking` alert. The forward bridge
already forwards alerts as DSS `event`s, so you'll see an event for an idle,
available vehicle — the cue to issue a `reassign_task` command back. That round trip
(dock → event → DSS decision → command → execution) is the full closed loop.

## Testing without the full DSS

The sim side is fully exercisable on its own:

```bash
# reachable vehicle -> executed
curl -X POST localhost:8000/command -H "content-type: application/json" \
  -d '{"command_id":"c1","vehicle_id":"UAV-1","action":"reroute","params":{"lat":51.05,"lon":1.55,"task":"Investigate"}}'

# inspect queue + recent acks
curl localhost:8000/commands
```

To dry-run the bridge translation before the DSS channel exists, point
`DSS_CMD_WS_URL` at a tiny local websocket server that sends one command frame in
the shape above; the bridge will translate it and POST to the sim.

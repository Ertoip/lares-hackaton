# Maritime Multi-Domain DSS Backend

FastAPI backend input layer for a Decision Support System, DSS, managing six external unmanned vehicles:

- `air_1`, `air_2`
- `surface_1`, `surface_2`
- `sub_1`, `sub_2`

The DSS treats these vehicles as external entities. It does not simulate them internally. Vehicles send direct JSON messages to one DSS WebSocket, and the backend validates those messages, updates in-memory state, derives communication/link status, and exposes debug REST endpoints.

## What This Backend Does

- Receives vehicle messages over one WebSocket: `WS /dss/ws/vehicles`
- Supports `heartbeat`, `telemetry`, `event`, and `link_state` messages
- Validates `vehicle_id`, `domain`, message shape, and heartbeat interval
- Maintains in-memory `vehicles` and `events` maps
- Derives link status from heartbeat age and subsurface blackout windows
- Recalculates link status in the background even when no new message arrives
- Exposes REST endpoints for health checks and state inspection

## What This Backend Does Not Do

- It does not include a frontend
- It does not simulate drones
- It does not persist state to a database
- It does not provide command output APIs yet
- It does not perform mission planning or autonomy decisions yet

## Project Structure

```text
dss_backend/
  __init__.py
  main.py          # FastAPI app setup and background task lifecycle
  models.py        # Pydantic validation models for incoming messages
  state.py         # In-memory vehicles/events maps and vehicle validation
  websocket.py     # WebSocket endpoint and message routing
  link_status.py   # Derived link-status logic
  routers.py       # REST debug endpoints
  requirements.txt # Runtime dependencies
  test_client.py   # Small WebSocket client for local testing
API.md             # Detailed API reference
README.md          # Developer guide
```

## Run Locally

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r dss_backend/requirements.txt
```

Run the API:

```bash
uvicorn dss_backend.main:app --reload --port 8001
```

The backend will be available at:

```text
http://localhost:8001
```

Port `8000` is often already used by other local services. This README uses `8001` for the DSS backend. If `8001` is also taken, choose another port:

```bash
uvicorn dss_backend.main:app --reload --port 8010
```

When using a custom port, pass the matching WebSocket URL to the test client:

```bash
DSS_WS_URL=ws://localhost:8010/dss/ws/vehicles python dss_backend/test_client.py
```

Open generated FastAPI docs:

```text
http://localhost:8001/docs
```

Run the sample WebSocket client in another terminal:

```bash
python dss_backend/test_client.py
```

## Allowed Vehicles

Only these six vehicles are accepted:

| Vehicle ID | Required Domain |
| --- | --- |
| `air_1` | `air` |
| `air_2` | `air` |
| `surface_1` | `surface` |
| `surface_2` | `surface` |
| `sub_1` | `subsurface` |
| `sub_2` | `subsurface` |

Every incoming vehicle message must include both `vehicle_id` and `domain`. The backend rejects messages where the domain does not match the vehicle ID.

Example invalid message:

```json
{
  "message_type": "heartbeat",
  "vehicle_id": "air_1",
  "domain": "surface"
}
```

The backend returns:

```json
{
  "ok": false,
  "error": "Domain does not match vehicle_id"
}
```

## WebSocket Endpoint

```http
WS /dss/ws/vehicles
```

This is the only vehicle input socket. All six vehicles send messages to this endpoint. The backend decides how to process the message using the `message_type` field.

Connection arguments:

| Argument Location | Argument | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Path | None | N/A | No | This WebSocket endpoint has no path arguments |
| Query | None | N/A | No | This WebSocket endpoint has no query arguments |
| Headers | Standard WebSocket headers | HTTP headers | Yes, handled by client library | Used by the WebSocket protocol upgrade |
| Message body | JSON object | object | Yes, per message | One direct vehicle message per WebSocket frame |

Connection URL:

```text
ws://localhost:8001/dss/ws/vehicles
```

Supported `message_type` values:

| Message Type | Purpose |
| --- | --- |
| `heartbeat` | Confirms that a vehicle is reachable right now |
| `telemetry` | Updates position, velocity, battery, sensors, capabilities, and current task |
| `event` | Stores a vehicle-reported event, detection, or alert |
| `link_state` | Updates communication context, especially planned subsurface blackout windows |

The backend does not use the old wrapper format with `type`, `source`, and `payload`. Send direct vehicle messages only.

## WebSocket Acknowledgements

Every valid message receives an acknowledgement:

```json
{
  "ok": true,
  "received_message_type": "heartbeat",
  "vehicle_id": "air_1"
}
```

Invalid messages receive an error response on the same WebSocket connection:

```json
{
  "ok": false,
  "error": "Invalid vehicle_id"
}
```

Validation errors include details from Pydantic:

```json
{
  "ok": false,
  "error": "Validation failed",
  "details": []
}
```

Successful acknowledgement fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `ok` | boolean | `true` when the message was valid and processed |
| `received_message_type` | string | The accepted incoming message type |
| `vehicle_id` | string | Vehicle ID from the accepted message |

Error acknowledgement fields:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `ok` | boolean | Yes | `false` when the message was rejected |
| `error` | string | Yes | Human-readable error reason |
| `details` | array | Only for schema validation failures | Pydantic validation detail objects |

Common `error` values:

| Error | Meaning |
| --- | --- |
| `Invalid message_type` | Missing or unsupported `message_type` |
| `Invalid vehicle_id` | `vehicle_id` is not one of the six allowed vehicles |
| `Domain does not match vehicle_id` | `domain` does not match the configured vehicle domain |
| `Message must be a JSON object` | Incoming WebSocket frame decoded as JSON but was not an object |
| `Validation failed` | JSON object failed Pydantic model validation |

## Message Flow

For every incoming WebSocket message, the backend performs this flow:

1. Parse JSON
2. Validate `message_type`
3. Validate the Pydantic schema for that message type
4. Check that `vehicle_id` is one of the six allowed vehicles
5. Check that `domain` matches the vehicle ID
6. Route the message by `message_type`
7. Update vehicle state or event state
8. Recalculate link status when relevant
9. Send an acknowledgement back through the WebSocket

## Common WebSocket Arguments

These arguments appear in every incoming vehicle message.

Unknown extra arguments are rejected. The Pydantic models use strict schemas with `extra="forbid"`, so send only the documented fields for each message type.

| Argument | Type | Required | Allowed Values or Validation | Meaning |
| --- | --- | --- | --- | --- |
| `message_type` | string | Yes | `heartbeat`, `telemetry`, `event`, `link_state` | Tells the DSS how to validate and route the message |
| `vehicle_id` | string | Yes | `air_1`, `air_2`, `surface_1`, `surface_2`, `sub_1`, `sub_2` | Identifies which external vehicle sent the message |
| `timestamp` | ISO 8601 datetime string | Yes | Example: `2026-01-01T12:00:00Z` | Vehicle-provided event time for the message |
| `domain` | string | Yes | `air`, `surface`, `subsurface` | Vehicle domain; must match the configured domain for `vehicle_id` |

Important distinction:

| Timestamp | Source | Used For |
| --- | --- | --- |
| `timestamp` | Vehicle message | Records when the vehicle says the message was produced |
| `last_heartbeat_received_at` | DSS server clock | Used by the DSS to derive link status from actual receive time |

## Shared Nested Arguments

These objects are reused by several message types.

### `position`

| Argument | Type | Required | Validation | Meaning |
| --- | --- | --- | --- | --- |
| `lat` | number | Yes | `-90` to `90` | Latitude in decimal degrees |
| `lon` | number | Yes | `-180` to `180` | Longitude in decimal degrees |
| `alt` | number or null | No | Any number | Altitude, mainly used by air vehicles |
| `depth` | number or null | No | `>= 0` | Depth below surface, mainly used by subsurface vehicles |

### `velocity`

| Argument | Type | Required | Validation | Meaning |
| --- | --- | --- | --- | --- |
| `speed_mps` | number | Yes | `>= 0` | Vehicle speed in meters per second |
| `heading_deg` | number | Yes | `0 <= heading_deg < 360` | Heading in degrees |

### `battery`

| Argument | Type | Required | Validation | Meaning |
| --- | --- | --- | --- | --- |
| `percentage` | number | Yes | `0` to `100` | Current battery percentage |
| `bingo_threshold` | number | Yes | `0` to `100` | Mission-defined low-battery threshold where the DSS should consider the vehicle near minimum safe reserve |

### `sensors`

| Argument | Type | Required | Allowed Values | Meaning |
| --- | --- | --- | --- | --- |
| `camera` | string | Yes | `ok`, `degraded`, `fault`, `unavailable` | Camera availability or health state |
| `radar` | string | Yes | `ok`, `degraded`, `fault`, `unavailable` | Radar availability or health state |
| `sonar` | string | Yes | `ok`, `degraded`, `fault`, `unavailable` | Sonar availability or health state |

### `capabilities`

| Argument | Type | Required | Meaning |
| --- | --- | --- | --- |
| `visual_isr` | boolean | Yes | Whether the vehicle can perform visual intelligence, surveillance, and reconnaissance |
| `radar_scan` | boolean | Yes | Whether the vehicle can perform radar scanning |
| `sonar_scan` | boolean | Yes | Whether the vehicle can perform sonar scanning |
| `relay_comms` | boolean | Yes | Whether the vehicle can act as a communications relay |

### `expected_next_contact_window`

| Argument | Type | Required | Validation | Meaning |
| --- | --- | --- | --- | --- |
| `start` | ISO 8601 datetime string | Yes when object is present | Must be before `end` | Start of the next expected contact window |
| `end` | ISO 8601 datetime string | Yes when object is present | Must be after `start` | End of the next expected contact window |

## Heartbeat Messages

Heartbeat messages are used to derive whether the vehicle link is `online`, `degraded`, `unstable`, or `lost_link`.

Example:

```json
{
  "message_type": "heartbeat",
  "vehicle_id": "air_1",
  "timestamp": "2026-01-01T12:00:00Z",
  "sequence": 184,
  "domain": "air",
  "communication_mode": "radio",
  "expected_interval_ms": 1000
}
```

Required heartbeat intervals:

| Domain | Required `expected_interval_ms` |
| --- | --- |
| `air` | `1000` |
| `surface` | `3000` |
| `subsurface` | `30000` |

If an air drone sends `expected_interval_ms = 3000`, the message is rejected because air drones are expected every 1 second.

Heartbeat arguments:

| Argument | Type | Required | Allowed Values or Validation | Meaning | State Effect |
| --- | --- | --- | --- | --- | --- |
| `message_type` | string | Yes | Must be `heartbeat` | Identifies this message as a heartbeat | Routes to heartbeat handler |
| `vehicle_id` | string | Yes | One of the six allowed vehicles | Vehicle that sent the heartbeat | Selects vehicle state entry |
| `timestamp` | ISO 8601 datetime string | Yes | Valid datetime | Vehicle-provided heartbeat timestamp | Stored as `link.last_heartbeat_at` |
| `sequence` | integer | Yes | `>= 0` | Monotonic or incrementing heartbeat sequence number from the vehicle | Stored in `last_heartbeat` for debugging |
| `domain` | string | Yes | Must match `vehicle_id` | Vehicle domain | Stored as `domain` |
| `communication_mode` | string | Yes | `radio`, `acoustic`, `satellite`, `cellular` | Communication channel used by the vehicle | Stored as `link.communication_mode` |
| `expected_interval_ms` | integer | Yes | `air=1000`, `surface=3000`, `subsurface=30000` | Expected heartbeat interval for this domain | Stored as `link.expected_interval_ms` and validated |

A heartbeat also stores `link.last_heartbeat_received_at` using the DSS server clock. That receive time is what the backend uses for link degradation calculations.

## Telemetry Messages

Telemetry messages update the vehicle's physical and mission state.

Example:

```json
{
  "message_type": "telemetry",
  "vehicle_id": "air_1",
  "timestamp": "2026-01-01T12:00:01Z",
  "domain": "air",
  "status": "active",
  "position": {
    "lat": 41.9028,
    "lon": 12.4964,
    "alt": 120
  },
  "velocity": {
    "speed_mps": 14.2,
    "heading_deg": 82
  },
  "battery": {
    "percentage": 76,
    "bingo_threshold": 20
  },
  "sensors": {
    "camera": "ok",
    "radar": "ok",
    "sonar": "unavailable"
  },
  "capabilities": {
    "visual_isr": true,
    "radar_scan": true,
    "sonar_scan": false,
    "relay_comms": false
  },
  "current_task_id": "task_scan_sector_a"
}
```

Telemetry updates these fields in the vehicle state:

| State Field | Meaning |
| --- | --- |
| `telemetry` | Full last telemetry message |
| `status` | Vehicle-reported operating status |
| `position` | Last reported latitude, longitude, altitude, or depth |
| `velocity` | Last reported speed and heading |
| `battery` | Battery percentage and bingo threshold |
| `sensors` | Sensor health states |
| `capabilities` | Vehicle capabilities |
| `current_task_id` | Current task identifier, if any |

Telemetry does not by itself prove the link is currently healthy. Link status is primarily based on heartbeat timing and explicit `link_state` messages.

Telemetry arguments:

| Argument | Type | Required | Allowed Values or Validation | Meaning | State Effect |
| --- | --- | --- | --- | --- | --- |
| `message_type` | string | Yes | Must be `telemetry` | Identifies this message as telemetry | Routes to telemetry handler |
| `vehicle_id` | string | Yes | One of the six allowed vehicles | Vehicle that sent telemetry | Selects vehicle state entry |
| `timestamp` | ISO 8601 datetime string | Yes | Valid datetime | Vehicle-provided telemetry timestamp | Stored inside full `telemetry` snapshot |
| `domain` | string | Yes | Must match `vehicle_id` | Vehicle domain | Stored as `domain` |
| `status` | string | Yes | `active`, `idle`, `standby`, `fault`, `returning`, `offline` | Vehicle-reported operating state | Stored as top-level `status` |
| `position` | object | Yes | See `position` arguments | Last reported geographic position | Stored as top-level `position` |
| `velocity` | object | Yes | See `velocity` arguments | Last reported movement vector | Stored as top-level `velocity` |
| `battery` | object | Yes | See `battery` arguments | Battery state and low-battery threshold | Stored as top-level `battery` |
| `sensors` | object | Yes | See `sensors` arguments | Sensor health or availability | Stored as top-level `sensors` |
| `capabilities` | object | Yes | See `capabilities` arguments | Capabilities currently advertised by the vehicle | Stored as top-level `capabilities` |
| `current_task_id` | string or null | No | Any string or null | Task currently assigned to or executed by the vehicle | Stored as `current_task_id` |

Telemetry is stored twice: as the full original `telemetry` snapshot and as extracted top-level fields for easier debugging.

## Event Messages

Event messages represent notable vehicle-reported observations or alerts.

Example:

```json
{
  "message_type": "event",
  "event_id": "evt_001",
  "timestamp": "2026-01-01T12:22:00Z",
  "vehicle_id": "surface_1",
  "domain": "surface",
  "event_kind": "unknown_contact",
  "severity": "high",
  "position": {
    "lat": 41.9101,
    "lon": 12.5012
  },
  "description": "Fast unknown contact detected near sector B",
  "metadata": {
    "ais": "off",
    "speed_knots": 35,
    "behavior": "suspicious"
  }
}
```

Events are stored in the global `events` map by `event_id`.

If another event arrives with the same `event_id`, it overwrites the previous event for now. This is acceptable for the current in-memory debug baseline.

Event arguments:

| Argument | Type | Required | Allowed Values or Validation | Meaning | State Effect |
| --- | --- | --- | --- | --- | --- |
| `message_type` | string | Yes | Must be `event` | Identifies this message as an event | Routes to event handler |
| `event_id` | string | Yes | Non-empty string | Unique event identifier from the vehicle or upstream source | Used as the key in the `events` map |
| `timestamp` | ISO 8601 datetime string | Yes | Valid datetime | Vehicle-provided time when the event occurred or was reported | Stored in event record |
| `vehicle_id` | string | Yes | One of the six allowed vehicles | Vehicle that reported the event | Stored in event record |
| `domain` | string | Yes | Must match `vehicle_id` | Vehicle domain | Stored in event record |
| `event_kind` | string | Yes | Non-empty string | Machine-readable event category such as `unknown_contact` | Stored in event record |
| `severity` | string | Yes | `low`, `medium`, `high`, `critical` | Event importance level | Stored in event record |
| `position` | object or null | No | See `position` arguments | Location associated with the event, if known | Stored in event record |
| `description` | string | Yes | Non-empty string | Human-readable event description | Stored in event record |
| `metadata` | object | No | Any JSON object | Extra event-specific fields such as AIS state, contact speed, or classification | Stored in event record |

`metadata` is intentionally flexible because different event kinds may need different supporting data.

## Link State Messages

Link state messages describe communication context. They are especially important for subsurface vehicles.

Example expected blackout:

```json
{
  "message_type": "link_state",
  "vehicle_id": "sub_1",
  "timestamp": "2026-01-01T12:05:00Z",
  "domain": "subsurface",
  "communication_mode": "acoustic",
  "status": "expected_blackout",
  "last_contact_at": "2026-01-01T12:00:00Z",
  "expected_next_contact_window": {
    "start": "2026-01-01T12:25:00Z",
    "end": "2026-01-01T12:30:00Z"
  }
}
```

This tells the DSS that `sub_1` is expected to be silent until its next contact window. Missing heartbeats during that period should not immediately become `lost_link`.

If `status` is `expected_blackout`, `expected_next_contact_window` is required.

Link-state arguments:

| Argument | Type | Required | Allowed Values or Validation | Meaning | State Effect |
| --- | --- | --- | --- | --- | --- |
| `message_type` | string | Yes | Must be `link_state` | Identifies this message as a link-state update | Routes to link-state handler |
| `vehicle_id` | string | Yes | One of the six allowed vehicles | Vehicle whose communication state is being updated | Selects vehicle state entry |
| `timestamp` | ISO 8601 datetime string | Yes | Valid datetime | Vehicle-provided time for this communication-state update | Stored in `last_link_state` |
| `domain` | string | Yes | Must match `vehicle_id` | Vehicle domain | Stored as `domain` |
| `communication_mode` | string | Yes | `radio`, `acoustic`, `satellite`, `cellular` | Communication mode relevant to this link state | Stored as `link.communication_mode` |
| `status` | string | Yes | `online`, `degraded`, `unstable`, `lost_link`, `expected_blackout`, `late_contact` | Vehicle-reported communication state or expectation | Stored as `link.reported_status`; derived status is stored as `link.status` |
| `last_contact_at` | ISO 8601 datetime string or null | No | Valid datetime or null | Last time the vehicle says it had contact | Stored as `link.last_contact_at` |
| `expected_next_contact_window` | object or null | Required when `status=expected_blackout` | See `expected_next_contact_window` arguments | Planned next time window when contact is expected | Stored as `link.expected_next_contact_window` |

For subsurface vehicles, `expected_blackout` prevents normal missing-heartbeat degradation until the contact window has passed.

## Derived Link Status

The backend recalculates link status for vehicles with link information once per second.

Air drones:

| Time Since Last Heartbeat | Link Status |
| --- | --- |
| `0-2s` | `online` |
| `2-5s` | `degraded` |
| `5-10s` | `unstable` |
| `>10s` | `lost_link` |

Surface drones:

| Time Since Last Heartbeat | Link Status |
| --- | --- |
| `0-6s` | `online` |
| `6-12s` | `degraded` |
| `12-20s` | `unstable` |
| `>20s` | `lost_link` |

Subsurface drones:

| Condition | Link Status |
| --- | --- |
| In expected blackout and before contact window end | `expected_blackout` |
| Past expected contact window end | `late_contact` |
| More than 5 minutes past contact window end | `lost_link` |
| Connected with heartbeat age `0-60s` | `online` |
| Connected with heartbeat age `60-120s` | `degraded` |
| Connected with heartbeat age `120-180s` | `unstable` |
| Connected with heartbeat age `>180s` | `lost_link` |

Vehicles that have never sent heartbeat or link-state information remain as empty dictionaries in the initial in-memory state.

## REST Debug Endpoints

These endpoints are intended for development and debugging. They expose the current in-memory DSS state.

## `GET /dss/health`

Basic health check endpoint.

Use this to confirm the FastAPI process is running.

Endpoint arguments:

| Argument Location | Argument | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Path | None | N/A | No | This endpoint has no path arguments |
| Query | None | N/A | No | This endpoint has no query arguments |
| Body | None | N/A | No | This endpoint does not accept a request body |

Response fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | string | Health status of the backend process; currently returns `ok` |

Status codes:

| Status Code | Meaning |
| --- | --- |
| `200` | Backend process is running and responded successfully |

Example request:

```bash
curl http://localhost:8001/dss/health
```

Example response:

```json
{
  "status": "ok"
}
```

## `GET /dss/state`

Returns the complete in-memory DSS debug state.

Endpoint arguments:

| Argument Location | Argument | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Path | None | N/A | No | This endpoint has no path arguments |
| Query | None | N/A | No | This endpoint has no query arguments |
| Body | None | N/A | No | This endpoint does not accept a request body |

Response fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `vehicles` | object | Complete vehicle state map keyed by vehicle ID |
| `events` | object | Complete event state map keyed by event ID |

`vehicles` contains exactly these top-level keys: `air_1`, `air_2`, `surface_1`, `surface_2`, `sub_1`, and `sub_2`.

`events` starts as an empty object and is populated by incoming `event` WebSocket messages.

Status codes:

| Status Code | Meaning |
| --- | --- |
| `200` | State returned successfully |

This includes both vehicle state and event state:

```json
{
  "vehicles": {},
  "events": {}
}
```

Example request:

```bash
curl http://localhost:8001/dss/state
```

Example initial response:

```json
{
  "vehicles": {
    "air_1": {},
    "air_2": {},
    "surface_1": {},
    "surface_2": {},
    "sub_1": {},
    "sub_2": {}
  },
  "events": {}
}
```

Use this endpoint when you want a full snapshot of what the DSS currently knows.

## `GET /dss/vehicles`

Returns only the vehicle state map.

Endpoint arguments:

| Argument Location | Argument | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Path | None | N/A | No | This endpoint has no path arguments |
| Query | None | N/A | No | This endpoint has no query arguments |
| Body | None | N/A | No | This endpoint does not accept a request body |

Response fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `air_1` | object | Current DSS state for air drone 1 |
| `air_2` | object | Current DSS state for air drone 2 |
| `surface_1` | object | Current DSS state for surface drone 1 |
| `surface_2` | object | Current DSS state for surface drone 2 |
| `sub_1` | object | Current DSS state for subsurface drone 1 |
| `sub_2` | object | Current DSS state for subsurface drone 2 |

Common vehicle-state fields after messages arrive:

| Field | Type | Present After | Meaning |
| --- | --- | --- | --- |
| `vehicle_id` | string | Any valid message for that vehicle | Vehicle identifier |
| `domain` | string | Any valid message for that vehicle | Vehicle domain |
| `last_heartbeat` | object | `heartbeat` | Full last heartbeat message |
| `last_link_state` | object | `link_state` | Full last link-state message |
| `telemetry` | object | `telemetry` | Full last telemetry message |
| `status` | string | `telemetry` | Vehicle operating status |
| `position` | object | `telemetry` | Last known vehicle position |
| `velocity` | object | `telemetry` | Last known vehicle velocity |
| `battery` | object | `telemetry` | Last known battery state |
| `sensors` | object | `telemetry` | Last known sensor states |
| `capabilities` | object | `telemetry` | Last known vehicle capabilities |
| `current_task_id` | string or null | `telemetry` | Current task ID |
| `link` | object | `heartbeat` or `link_state` | Derived and reported communication status |

Common `link` fields:

| Field | Type | Present After | Meaning |
| --- | --- | --- | --- |
| `status` | string | `heartbeat`, `link_state`, or background recalculation | DSS-derived link status |
| `status_updated_at` | ISO 8601 datetime string | Link status calculation | Server time when link status was last recalculated |
| `last_heartbeat_at` | ISO 8601 datetime string | `heartbeat` | Vehicle-provided heartbeat timestamp |
| `last_heartbeat_received_at` | ISO 8601 datetime string | `heartbeat` | DSS receive time for the last heartbeat |
| `communication_mode` | string | `heartbeat` or `link_state` | Communication mode such as `radio` or `acoustic` |
| `expected_interval_ms` | integer | `heartbeat` | Expected heartbeat interval in milliseconds |
| `reported_status` | string | `link_state` | Link status reported by the vehicle or external comms source |
| `last_contact_at` | ISO 8601 datetime string or null | `link_state` | Last known contact time reported in link-state message |
| `expected_next_contact_window` | object or null | `link_state` | Planned contact window for expected blackout cases |

Status codes:

| Status Code | Meaning |
| --- | --- |
| `200` | Vehicle map returned successfully |

Example request:

```bash
curl http://localhost:8001/dss/vehicles
```

Example response after `air_1` sends heartbeat and telemetry:

```json
{
  "air_1": {
    "vehicle_id": "air_1",
    "domain": "air",
    "status": "active",
    "position": {
      "lat": 41.9028,
      "lon": 12.4964,
      "alt": 120,
      "depth": null
    },
    "link": {
      "status": "online",
      "communication_mode": "radio",
      "expected_interval_ms": 1000
    }
  },
  "air_2": {},
  "surface_1": {},
  "surface_2": {},
  "sub_1": {},
  "sub_2": {}
}
```

Actual responses may include additional fields such as `last_heartbeat`, `telemetry`, `status_updated_at`, and timestamp values.

## `GET /dss/vehicles/{vehicle_id}`

Returns the current state for one vehicle.

Endpoint arguments:

| Argument Location | Argument | Type | Required | Allowed Values | Meaning |
| --- | --- | --- | --- | --- | --- |
| Path | `vehicle_id` | string | Yes | `air_1`, `air_2`, `surface_1`, `surface_2`, `sub_1`, `sub_2` | Selects which vehicle state to return |
| Query | None | N/A | No | N/A | This endpoint has no query arguments |
| Body | None | N/A | No | N/A | This endpoint does not accept a request body |

Response fields:

| Field | Type | Meaning |
| --- | --- | --- |
| Entire response | object | Vehicle state object for the selected vehicle |

The object can be empty if the vehicle ID is valid but no messages have arrived yet.

The possible vehicle-state fields are the same fields documented in `GET /dss/vehicles`.

Status codes:

| Status Code | Meaning |
| --- | --- |
| `200` | Vehicle exists and its state was returned |
| `404` | `vehicle_id` is not one of the six allowed vehicles |

Example request:

```bash
curl http://localhost:8001/dss/vehicles/air_1
```

Example response for a known vehicle that has not sent any messages yet:

```json
{}
```

Example response for an invalid vehicle ID:

```json
{
  "detail": "Vehicle not found"
}
```

Invalid vehicle IDs return HTTP `404`.

Valid vehicle IDs are always present in the vehicle map, even if their current state is still empty.

## `GET /dss/events`

Returns all stored events keyed by `event_id`.

Endpoint arguments:

| Argument Location | Argument | Type | Required | Meaning |
| --- | --- | --- | --- | --- |
| Path | None | N/A | No | This endpoint has no path arguments |
| Query | None | N/A | No | This endpoint has no query arguments |
| Body | None | N/A | No | This endpoint does not accept a request body |

Response fields:

| Field | Type | Meaning |
| --- | --- | --- |
| Top-level keys | string | Event IDs such as `evt_001` |
| Top-level values | object | Event records containing the fields from the incoming `event` message |

Each event record contains the event arguments documented in the `Event Messages` section: `message_type`, `event_id`, `timestamp`, `vehicle_id`, `domain`, `event_kind`, `severity`, `position`, `description`, and `metadata`.

Status codes:

| Status Code | Meaning |
| --- | --- |
| `200` | Event map returned successfully |

Example request:

```bash
curl http://localhost:8001/dss/events
```

Example initial response:

```json
{}
```

Example response after an event:

```json
{
  "evt_001": {
    "message_type": "event",
    "event_id": "evt_001",
    "timestamp": "2026-01-01T12:22:00Z",
    "vehicle_id": "surface_1",
    "domain": "surface",
    "event_kind": "unknown_contact",
    "severity": "high",
    "position": {
      "lat": 41.9101,
      "lon": 12.5012,
      "alt": null,
      "depth": null
    },
    "description": "Fast unknown contact detected near sector B",
    "metadata": {
      "ais": "off",
      "speed_knots": 35,
      "behavior": "suspicious"
    }
  }
}
```

## Local WebSocket Test Client

The included client connects to:

```text
ws://localhost:8001/dss/ws/vehicles
```

It sends:

- one `heartbeat` message for `air_1`
- one `telemetry` message for `air_1`

Run it after starting the API:

```bash
python dss_backend/test_client.py
```

Expected output:

```json
{"ok":true,"received_message_type":"heartbeat","vehicle_id":"air_1"}
{"ok":true,"received_message_type":"telemetry","vehicle_id":"air_1"}
```

Then inspect state:

```bash
curl http://localhost:8001/dss/vehicles/air_1
```

## API Reference

This README is the developer quick-start guide. See `API.md` for the fuller API contract and additional examples.

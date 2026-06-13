import { useEffect, useRef, useState, useCallback } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Tooltip,
  Popup,
  Circle,
  Polyline,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";


const API = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";
const CENTER = [51.05, 1.55];

// --------------------------------------------------------------------- //
// Status -> colour
// --------------------------------------------------------------------- //
function statusColor(v) {
  if (v.submerged) return "#9aa7b0"; // grey — silent/submerged
  switch (v.status) {
    case "nominal":
      return "#2ecc71"; // green
    case "degraded":
      return "#f1c40f"; // yellow
    case "lost_comms":
    case "bingo":
      return "#e74c3c"; // red


    default:
      return "#2ecc71";
  }
}

// Highlight colour used while a vehicle is awaiting a waypoint pick.
const SELECT_COLOR = "#2f00ff";

// --------------------------------------------------------------------- //
// SVG marker icons per vehicle type
// --------------------------------------------------------------------- //
function vehicleIcon(v, selected = false) {
  // While picking a direction/waypoint, the selected vehicle turns cyan and
  // gets a stroke + pulsing halo so it's obvious which one you're commanding.
  const color = selected ? SELECT_COLOR : statusColor(v);
  const stroke = selected ? "#00e5ff" : "#06222e";
  const strokeWidth = selected ? 3 : 2;
  const heading = v.heading || 0;
  let shape;

  if (v.type === "UAV") {
    // triangle, rotated to heading
    shape = `<polygon points="14,2 26,26 2,26"
      fill="${color}" stroke="${stroke}" stroke-width="${strokeWidth}"
      transform="rotate(${heading} 14 14)"/>`;
  } else if (v.type === "USV") {
    // boat / hull shape, rotated to heading
    shape = `<path d="M14 2 L22 12 L20 26 L8 26 L6 12 Z"
      fill="${color}" stroke="${stroke}" stroke-width="${strokeWidth}"
      transform="rotate(${heading} 14 14)"/>`;
  } else {
    // UUV — circle
    shape = `<circle cx="14" cy="14" r="11"
      fill="${color}" stroke="${stroke}" stroke-width="${strokeWidth}"/>`;
  }

  const halo = selected
    ? `<circle cx="14" cy="14" r="13" fill="none" stroke="#00e5ff"
        stroke-width="1.5" opacity="0.8"/>`
    : "";

  const html = `<svg width="28" height="28" viewBox="0 0 28 28"
    xmlns="http://www.w3.org/2000/svg">${halo}${shape}</svg>`;

  return L.divIcon({
    html,
    className: selected ? "veh-icon selected" : "veh-icon",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

// Real AIS traffic — a small grey vessel chevron rotated to its heading.
function aisIcon(heading = 0) {
  const html = `<svg width="22" height="22" viewBox="0 0 22 22"
    xmlns="http://www.w3.org/2000/svg">
    <path d="M11 2 L17 19 L11 15 L5 19 Z"
      fill="#9aa7b0" stroke="#5b6770" stroke-width="1.5"
      transform="rotate(${heading} 11 11)"/></svg>`;
  return L.divIcon({
    html,
    className: "ais-icon",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function contactIcon() {
  const html = `<svg width="26" height="26" viewBox="0 0 26 26"
    xmlns="http://www.w3.org/2000/svg">
    <rect x="6" y="6" width="14" height="14"
      transform="rotate(45 13 13)"
      fill="#e74c3c" stroke="#3a0000" stroke-width="2"/></svg>`;
  return L.divIcon({
    html,
    className: "contact-icon",
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

const CONTACT_ICON = contactIcon();

// --------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------- //
function ageSeconds(ts) {
  return Math.max(0, Math.round(Date.now() / 1000 - ts));
}

function fmtBlackout(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

// Captures map clicks while in waypoint-assignment mode.
function MapClickHandler({ active, onPick }) {
  useMapEvents({
    click(e) {
      if (active) onPick(e.latlng);
    },
  });
  return null;
}

// --------------------------------------------------------------------- //
// Main component
// --------------------------------------------------------------------- //
export default function App() {
  const [vehicles, setVehicles] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [aisVessels, setAisVessels] = useState([]);
  const [weather, setWeather] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [acked, setAcked] = useState(new Set());
  const [simTime, setSimTime] = useState(0);
  const [connected, setConnected] = useState(false);
  const [estimates, setEstimates] = useState({}); // vid -> estimate
  const [assignMode, setAssignMode] = useState(null); // vehicle id awaiting waypoint

  const wsRef = useRef(null);

  // ---- WebSocket ----
  useEffect(() => {
    let stop = false;
    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stop) setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        setVehicles(msg.vehicles || []);
        setContacts(msg.contacts || []);
        setAisVessels(msg.ais || []);
        setWeather(msg.weather || null);
        setAlerts(msg.alerts || []);
        setSimTime(msg.sim_time_sec || 0);
      };
    }
    connect();
    return () => {
      stop = true;
      wsRef.current && wsRef.current.close();
    };
  }, []);

  // ---- Poll /estimate for submerged UUVs every 3s ----
  useEffect(() => {
    const submerged = vehicles.filter((v) => v.submerged).map((v) => v.id);
    if (submerged.length === 0) {
      setEstimates({});
      return;
    }
    let cancelled = false;
    async function poll() {
      const out = {};
      for (const id of submerged) {
        try {
          const r = await fetch(`${API}/estimate/${id}`);
          const data = await r.json();
          if (data.uncertainty_radius_m != null) out[id] = data;
        } catch (e) {
          /* ignore transient errors */
        }
      }
      if (!cancelled) setEstimates(out);
    }
    poll();
    const t = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [vehicles.map((v) => `${v.id}:${v.submerged}`).join(",")]);

  // ---- Esc cancels waypoint-assignment mode ----
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") setAssignMode(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ---- Actions ----
  const injectEvent = useCallback(async (type) => {
    try {
      await fetch(`${API}/inject/${type}`, { method: "POST" });
    } catch (e) {
      /* ignore */
    }
  }, []);

  const handleWaypointPick = useCallback(
    async (latlng) => {
      const vid = assignMode;
      if (!vid) return;
      setAssignMode(null);
      try {
        await fetch(`${API}/assign`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            vehicle_id: vid,
            task: `Proceed to waypoint`,
            lat: latlng.lat,
            lon: latlng.lng,
          }),
        });
      } catch (e) {
        /* ignore */
      }
    },
    [assignMode]
  );

  const ackAlert = (key) =>
    setAcked((prev) => new Set(prev).add(key));

  // Build a stable key per alert and filter acknowledged ones; show max 3.
  const visibleAlerts = alerts
    .map((a, i) => ({ ...a, _key: `${a.type}-${a.ts}-${a.vehicle || i}` }))
    .filter((a) => !acked.has(a._key))
    .slice(-3)
    .reverse();

  return (
    <div className="app">
      <div className="map-wrap">
        <div className={`conn-badge ${connected ? "on" : "off"}`}>
          {connected ? "● LIVE" : "○ reconnecting…"}
        </div>

        <div className="inject-bar">
          <button onClick={() => injectEvent("acoustic_loss")}>
            Inject: Acoustic loss
          </button>
          <button onClick={() => injectEvent("threat_contact")}>
            Inject: Threat contact
          </button>
          <button onClick={() => injectEvent("sensor_failure")}>
            Inject: Sensor failure
          </button>
          <button onClick={() => injectEvent("bingo_warning")}>
            Inject: Bingo warning
          </button>
        </div>

        {assignMode && (
          <div className="waypoint-banner">
            Click on the map to set a waypoint for {assignMode} (Esc to cancel)
          </div>
        )}

        <div className="sim-clock">SIM T+{simTime}s</div>

        <MapContainer center={CENTER} zoom={11} zoomControl={true}>
          <TileLayer
            attribution='&copy; OpenStreetMap contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <MapClickHandler active={!!assignMode} onPick={handleWaypointPick} />

          {/* Vehicles */}
          {vehicles.map((v) => {
            const est = estimates[v.id];
            // Submerged UUVs are drawn at their estimated position if known.
            const pos =
              v.submerged && est ? [est.lat, est.lon] : [v.lat, v.lon];
            return (
              <Marker
                key={v.id}
                position={pos}
                icon={vehicleIcon(v, assignMode === v.id)}
                zIndexOffset={assignMode === v.id ? 1000 : 0}
              >
                <Tooltip direction="top" offset={[0, -14]}>
                  <div className="veh-tooltip">
                    <b>{v.id}</b> ({v.type})<br />
                    Status: {v.submerged ? "submerged" : v.status}
                    <br />
                    Battery: {v.battery_pct}%<br />
                    Last contact: {ageSeconds(v.last_contact_ts)}s ago
                    <br />
                    Task: {v.current_task}
                    <br />
                    Link: {v.link_type} ({Math.round(v.link_quality * 100)}%)
                  </div>
                </Tooltip>
                <Popup>
                  <div>
                    <b>{v.id}</b> — {v.current_task}
                    <br />
                    <button
                      className="assign-btn"
                      onClick={() => setAssignMode(v.id)}
                    >
                      Assign Waypoint
                    </button>
                  </div>
                </Popup>
              </Marker>
            );
          })}

          {/* Assigned-waypoint dashed lines */}
          {vehicles
            .filter((v) => v.waypoint)
            .map((v) => {
              const est = estimates[v.id];
              const from =
                v.submerged && est ? [est.lat, est.lon] : [v.lat, v.lon];
              return (
                <Polyline
                  key={`wp-${v.id}`}
                  positions={[from, [v.waypoint.lat, v.waypoint.lon]]}
                  pathOptions={{
                    color: "#1f6feb",
                    dashArray: "8 8",
                    weight: 2,
                  }}
                />
              );
            })}

          {/* Submerged-UUV uncertainty circles + blackout labels */}
          {vehicles
            .filter((v) => v.submerged && estimates[v.id])
            .map((v) => {
              const est = estimates[v.id];
              return (
                <Circle
                  key={`est-${v.id}`}
                  center={[est.lat, est.lon]}
                  radius={est.uncertainty_radius_m}
                  pathOptions={{
                    color: "#3b82f6",
                    fillColor: "#3b82f6",
                    fillOpacity: 0.18,
                    weight: 1,
                  }}
                >
                  <Tooltip permanent direction="bottom">
                    {v.id} — In blackout —{" "}
                    {fmtBlackout(est.blackout_duration_sec)} (±
                    {Math.round(est.uncertainty_radius_m)}m)
                  </Tooltip>
                </Circle>
              );
            })}

          {/* Real AIS traffic (grey) */}
          {aisVessels.map((s) => (
            <Marker
              key={`ais-${s.mmsi}`}
              position={[s.lat, s.lon]}
              icon={aisIcon(s.heading)}
            >
              <Tooltip direction="top" offset={[0, -10]}>
                <div className="veh-tooltip">
                  <b>{s.name}</b> (AIS)
                  <br />
                  MMSI: {s.mmsi}
                  <br />
                  Speed: {s.sog_knots} kn
                  <br />
                  Heading: {Math.round(s.heading)}°
                </div>
              </Tooltip>
            </Marker>
          ))}

          {/* Threat contacts */}
          {contacts.map((c) => (
            <Marker
              key={c.id}
              position={[c.lat, c.lon]}
              icon={CONTACT_ICON}
            >
              <Tooltip direction="top" offset={[0, -10]}>
                <div className="veh-tooltip">
                  <b>{c.id}</b> (threat)
                  <br />
                  Speed: {c.speed_knots} kn
                  <br />
                  Behavior: {c.behavior}
                  <br />
                  AIS: {c.ais ? "yes" : "NO (dark)"}
                </div>
              </Tooltip>
            </Marker>
          ))}
        </MapContainer>
      </div>

      {/* ---- Sidebar ---- */}
      <Sidebar
        vehicles={vehicles}
        alerts={visibleAlerts}
        weather={weather}
        aisCount={aisVessels.length}
        onAck={ackAlert}
        onSelect={(id) => setAssignMode(id)}
      />
    </div>
  );
}

// --------------------------------------------------------------------- //
// Sidebar
// --------------------------------------------------------------------- //
function fmtNum(n, unit = "", digits = 1) {
  return n == null ? "—" : `${Number(n).toFixed(digits)}${unit}`;
}

function Sidebar({ vehicles, alerts, weather, aisCount, onAck, onSelect }) {
  return (
    <div className="sidebar">
      <div className="sidebar-section">
        <div className="sidebar-header">Fleet ({vehicles.length})</div>
        <div className="vehicle-list">
          {vehicles.map((v) => (
            <div
              key={v.id}
              className="vehicle-row"
              onClick={() => onSelect(v.id)}
              title="Click then pick a waypoint on the map"
            >
              <span
                className="dot"
                style={{ background: statusColor(v) }}
              />
              <span className="vid">{v.id}</span>
              <span>{v.submerged ? "submerged" : v.status}</span>
              <span className="vmeta">{v.battery_pct}%</span>
            </div>
          ))}
        </div>
      </div>

      <div className="sidebar-section" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <div className="sidebar-header">Alerts</div>
        <div className="alerts">
          {alerts.length === 0 && (
            <div style={{ color: "#5f7c8c", fontSize: 12, padding: 6 }}>
              No active alerts.
            </div>
          )}
          {alerts.map((a) => (
            <div key={a._key} className={`alert-card ${a.type}`}>
              <div className="atype">{a.type.replace(/_/g, " ")}</div>
              <div className="amsg">{a.message}</div>
              <div className="arow">
                <span className="ats">
                  {a.vehicle ? `${a.vehicle} · ` : ""}T+{a.sim_time_sec}s
                </span>
                <button className="ack" onClick={() => onAck(a._key)}>
                  Acknowledge
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="env-strip">
        {weather && !weather.error ? (
          <>
            <div className="env-row">
              🌊 Sea state {weather.sea_state} — {weather.sea_state_label}
            </div>
            <div className="env-row">
              Waves {fmtNum(weather.wave_height_m, " m")} @{" "}
              {fmtNum(weather.wave_period_s, " s")} ·{" "}
              {Math.round(weather.wave_direction_deg ?? 0)}°
            </div>
            <div className="env-row">
              💨 Wind {fmtNum(weather.wind_speed_kn, " kn")} ·{" "}
              {Math.round(weather.wind_direction_deg ?? 0)}° · 🌡{" "}
              {fmtNum(weather.air_temp_c, "°C")}
            </div>
            <div className="env-src">
              Open-Meteo · AIS traffic: {aisCount}
            </div>
          </>
        ) : (
          <div className="env-row">🌊 Awaiting live conditions…</div>
        )}
      </div>
    </div>
  );
}

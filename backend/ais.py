"""Real-world vessel traffic from the AISStream.io live feed.

AISStream broadcasts terrestrial/satellite AIS over a WebSocket. We subscribe
to the Strait of Messina bounding box and keep a rolling table of recently seen
vessels, which the UI draws in grey as background "real" traffic alongside the
simulated fleet.

A free API key is required (https://aisstream.io). Provide it via the
AISSTREAM_API_KEY environment variable. If it is missing, the service stays
dormant and simply reports no traffic — the rest of the app is unaffected.
"""

import asyncio
import json
import os
import time

import websockets

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

# Operating-area bounding box, matching the simulator's box.
# AISStream wants [[ [lat_min, lon_min], [lat_max, lon_max] ]].
BBOX = [[[38.05, 15.25], [38.35, 15.75]]]

STALE_SEC = 600         # drop vessels not heard from in 10 min
RECONNECT_SEC = 10      # wait before reconnecting after a drop


class AISService:
    """Maintains a live dict of nearby AIS vessels (keyed by MMSI)."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("AISSTREAM_API_KEY")
        self._vessels: dict[str, dict] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def vessels(self) -> list[dict]:
        """Active (non-stale) vessels as a list for broadcast."""
        now = time.time()
        return [
            v for v in self._vessels.values()
            if now - v["ts"] <= STALE_SEC
        ]

    def _ingest(self, msg: dict):
        if msg.get("MessageType") != "PositionReport":
            return
        meta = msg.get("MetaData", {})
        report = msg.get("Message", {}).get("PositionReport", {})
        mmsi = str(meta.get("MMSI") or report.get("UserID") or "")
        if not mmsi:
            return
        lat = meta.get("latitude", report.get("Latitude"))
        lon = meta.get("longitude", report.get("Longitude"))
        if lat is None or lon is None:
            return
        self._vessels[mmsi] = {
            "mmsi": mmsi,
            "name": (meta.get("ShipName") or "").strip() or mmsi,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "heading": report.get("TrueHeading") or report.get("Cog") or 0,
            "sog_knots": report.get("Sog", 0),
            "ts": time.time(),
        }

    async def run(self):
        """Background loop: connect, subscribe, ingest, reconnect on failure."""
        if not self.enabled:
            return  # no key -> stay dormant, no real traffic

        subscribe = {
            "APIKey": self.api_key,
            "BoundingBoxes": BBOX,
            "FilterMessageTypes": ["PositionReport"],
        }
        while True:
            try:
                async with websockets.connect(AISSTREAM_URL) as ws:
                    await ws.send(json.dumps(subscribe))
                    async for raw in ws:
                        try:
                            self._ingest(json.loads(raw))
                        except Exception:
                            continue  # skip malformed frames
            except Exception:
                # Connection dropped / refused; back off and retry.
                await asyncio.sleep(RECONNECT_SEC)

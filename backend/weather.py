"""Live marine + atmospheric conditions from the Open-Meteo API.

Open-Meteo is free and key-less. We poll two endpoints for a representative
point inside the Strait of Messina:

  * the Marine API  -> wave height / period / direction
  * the Forecast API -> wind speed / direction, air temperature

Results are cached and refreshed on a slow cadence (the sea state does not
change second-to-second). The blocking `requests` calls are run off the event
loop via `asyncio.to_thread`.
"""

import asyncio
import time

import requests

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# A point that is reliably on water inside the operating area.
SAMPLE_LAT = 38.15
SAMPLE_LON = 15.62

REFRESH_SEC = 600  # 10 minutes


def _douglas_sea_state(wave_height_m: float):
    """Map significant wave height (m) to the WMO Douglas sea-state scale."""
    table = [
        (0.0, 0, "Calm (glassy)"),
        (0.1, 1, "Calm (rippled)"),
        (0.5, 2, "Smooth"),
        (1.25, 3, "Slight"),
        (2.5, 4, "Moderate"),
        (4.0, 5, "Rough"),
        (6.0, 6, "Very rough"),
        (9.0, 7, "High"),
        (14.0, 8, "Very high"),
    ]
    code, label = 9, "Phenomenal"
    for threshold, c, lbl in table:
        if wave_height_m <= threshold:
            code, label = c, lbl
            break
    return code, label


class WeatherService:
    """Holds the latest fetched conditions and refreshes them periodically."""

    def __init__(self, lat: float = SAMPLE_LAT, lon: float = SAMPLE_LON):
        self.lat = lat
        self.lon = lon
        self._data: dict | None = None

    @property
    def current(self) -> dict | None:
        return self._data

    def _fetch_blocking(self) -> dict:
        marine = requests.get(
            MARINE_URL,
            params={
                "latitude": self.lat,
                "longitude": self.lon,
                "current": "wave_height,wave_period,wave_direction",
            },
            timeout=15,
        ).json().get("current", {})

        forecast = requests.get(
            FORECAST_URL,
            params={
                "latitude": self.lat,
                "longitude": self.lon,
                "current": "wind_speed_10m,wind_direction_10m,temperature_2m",
                "wind_speed_unit": "kn",
            },
            timeout=15,
        ).json().get("current", {})

        wave_h = marine.get("wave_height")
        sea_state, sea_label = _douglas_sea_state(wave_h if wave_h is not None else 0.0)

        return {
            "wave_height_m": wave_h,
            "wave_period_s": marine.get("wave_period"),
            "wave_direction_deg": marine.get("wave_direction"),
            "wind_speed_kn": forecast.get("wind_speed_10m"),
            "wind_direction_deg": forecast.get("wind_direction_10m"),
            "air_temp_c": forecast.get("temperature_2m"),
            "sea_state": sea_state,
            "sea_state_label": sea_label,
            "source": "open-meteo",
            "updated_ts": time.time(),
        }

    async def refresh_once(self):
        try:
            self._data = await asyncio.to_thread(self._fetch_blocking)
        except Exception as exc:  # network hiccups must not kill the loop
            if self._data is None:
                self._data = {"error": str(exc), "source": "open-meteo"}

    async def run(self):
        """Background loop: fetch immediately, then every REFRESH_SEC."""
        while True:
            await self.refresh_once()
            await asyncio.sleep(REFRESH_SEC)

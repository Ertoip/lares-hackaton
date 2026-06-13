import requests

r = requests.get(
    "https://marine-api.open-meteo.com/v1/marine",
    params={
        "latitude": 38.2,
        "longitude": 15.5,
        "current": "wave_height,wave_direction,wave_period"
    }
)
print(r.json())
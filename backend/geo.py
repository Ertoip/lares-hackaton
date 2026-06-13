"""Land / water helpers.

Backed by `global-land-mask`, an offline ~1 km land/ocean raster, so the
checks are fast and need no network. Used to keep surface and subsurface
vehicles on the water (an underwater drone has no business driving across
Sicily).
"""

import math

from global_land_mask import globe


def is_water(lat: float, lon: float) -> bool:
    """True if the point falls on ocean rather than land."""
    return bool(globe.is_ocean(lat, lon))


def snap_to_water(lat: float, lon: float,
                  max_radius_deg: float = 0.12,
                  step_deg: float = 0.004):
    """Return the nearest ocean point to (lat, lon).

    Spirals outward in rings until an ocean cell is found. If the point is
    already on water it is returned unchanged. Falls back to the original
    coordinates if no water is found within `max_radius_deg`.
    """
    if is_water(lat, lon):
        return lat, lon

    r = step_deg
    while r <= max_radius_deg:
        for ang in range(0, 360, 12):
            dlat = r * math.cos(math.radians(ang))
            dlon = r * math.sin(math.radians(ang))
            if is_water(lat + dlat, lon + dlon):
                return round(lat + dlat, 6), round(lon + dlon, 6)
        r += step_deg
    return lat, lon

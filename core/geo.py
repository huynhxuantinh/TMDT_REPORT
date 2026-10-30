"""Normalize real GIS points without converting missing/invalid values to zero."""
import math


def point_to_latlon(point):
    if point is None or point.empty:
        return None, None
    try:
        location = point.clone()
        if location.srid and location.srid != 4326:
            location.transform(4326)
        lon, lat = location.x, location.y
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (ValueError, TypeError, OverflowError):
        pass
    return None, None

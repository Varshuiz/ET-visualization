from math import radians, cos, sin, asin, sqrt


ALBERTA_STATIONS_COORDS = {
    "Lethbridge": {"lat": 49.6942, "lon": -112.8328},
    "Calgary": {"lat": 51.1139, "lon": -114.0203},
    "Edmonton": {"lat": 53.3097, "lon": -113.5800},
    "Red Deer": {"lat": 52.1822, "lon": -113.8939},
    "Medicine Hat": {"lat": 50.0189, "lon": -110.7208},
    "Medicine Lake Auto": {"lat": 54.0181, "lon": -112.9767},
    "Brooks": {"lat": 50.5644, "lon": -111.8986},
    "Vauxhall": {"lat": 50.0500, "lon": -112.1333},
    "Taber": {"lat": 49.7833, "lon": -112.1500},
    "Grande Prairie": {"lat": 55.1796, "lon": -118.8850},
    "Fort McMurray": {"lat": 56.6532, "lon": -111.2217},
}


def _haversine_km(lon1, lat1, lon2, lat2):
    """Calculate distance between two points on Earth (km)."""
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371 * c


def find_nearest_alberta_station(latitude, longitude):
    """Find nearest known Alberta station to given coordinates."""
    nearest = None
    min_distance = float("inf")

    for station_name, coords in ALBERTA_STATIONS_COORDS.items():
        distance = _haversine_km(longitude, latitude, coords["lon"], coords["lat"])
        if distance < min_distance:
            min_distance = distance
            nearest = {
                "name": station_name,
                "latitude": coords["lat"],
                "longitude": coords["lon"],
                "distance": distance,
            }

    return nearest

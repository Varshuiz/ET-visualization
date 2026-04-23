import requests
from django.http import JsonResponse


def geocode_location(place_name, province="Alberta", country="Canada"):
    """Convert place name to coordinates using Nominatim."""
    url = "https://nominatim.openstreetmap.org/search"
    search_query = f"{place_name}, {province}, {country}"
    params = {"q": search_query, "format": "json", "limit": 1, "addressdetails": 1}
    headers = {"User-Agent": "ET-Calculator/1.0 (Agricultural Research)"}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            raise ValueError(f"Geocoding failed: HTTP {response.status_code}")
        results = response.json()
        if not results:
            raise ValueError(f"Location '{place_name}' not found")
        result = results[0]
        return {
            "latitude": float(result["lat"]),
            "longitude": float(result["lon"]),
            "display_name": result["display_name"],
            "place_name": place_name,
        }
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Geocoding error: {str(e)}") from e
    except Exception as e:
        raise ValueError(f"Error processing location: {str(e)}") from e


def reverse_geocode(latitude, longitude):
    """Convert coordinates to place name."""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": latitude, "lon": longitude, "format": "json", "zoom": 10}
    headers = {"User-Agent": "ET-Calculator/1.0 (Agricultural Research)"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if "display_name" in result:
                return result["display_name"]
        return f"{latitude:.4f}°N, {abs(longitude):.4f}°W"
    except Exception:
        return f"{latitude:.4f}°N, {abs(longitude):.4f}°W"


ALBERTA_LOCATIONS = {
    "Calgary": {"lat": 51.0447, "lon": -114.0719, "twp": 24, "rge": 1, "mer": "5th"},
    "Edmonton": {"lat": 53.5461, "lon": -113.4938, "twp": 53, "rge": 25, "mer": "4th"},
    "Red Deer": {"lat": 52.2681, "lon": -113.8111, "twp": 38, "rge": 27, "mer": "4th"},
    "Lethbridge": {"lat": 49.6942, "lon": -112.8328, "twp": 9, "rge": 22, "mer": "4th"},
    "Medicine Hat": {"lat": 50.0417, "lon": -110.6775, "twp": 13, "rge": 6, "mer": "4th"},
    "Grande Prairie": {"lat": 55.1707, "lon": -118.7947, "twp": 72, "rge": 6, "mer": "6th"},
    "Fort McMurray": {"lat": 56.7267, "lon": -111.3790, "twp": 88, "rge": 9, "mer": "4th"},
    "Medicine Lake Auto": {"lat": 54.0181, "lon": -112.9767, "twp": 52, "rge": 3, "mer": "5th"},
    "Airdrie": {"lat": 51.2917, "lon": -114.0144, "twp": 26, "rge": 1, "mer": "5th"},
    "St. Albert": {"lat": 53.6303, "lon": -113.6258, "twp": 54, "rge": 25, "mer": "4th"},
    "Spruce Grove": {"lat": 53.5450, "lon": -113.9006, "twp": 53, "rge": 26, "mer": "4th"},
    "Leduc": {"lat": 53.2594, "lon": -113.5514, "twp": 50, "rge": 25, "mer": "4th"},
    "Okotoks": {"lat": 50.7264, "lon": -113.9764, "twp": 21, "rge": 29, "mer": "4th"},
    "Cochrane": {"lat": 51.1889, "lon": -114.4678, "twp": 25, "rge": 4, "mer": "5th"},
    "Camrose": {"lat": 53.0158, "lon": -112.8403, "twp": 47, "rge": 20, "mer": "4th"},
    "Lloydminster": {"lat": 53.2783, "lon": -110.0050, "twp": 50, "rge": 1, "mer": "4th"},
    "Brooks": {"lat": 50.5644, "lon": -111.8986, "twp": 19, "rge": 14, "mer": "4th"},
    "Wetaskiwin": {"lat": 52.9694, "lon": -113.3769, "twp": 46, "rge": 25, "mer": "4th"},
    "Cold Lake": {"lat": 54.4639, "lon": -110.1817, "twp": 63, "rge": 4, "mer": "4th"},
    "High River": {"lat": 50.5831, "lon": -113.8711, "twp": 19, "rge": 28, "mer": "4th"},
    "Sylvan Lake": {"lat": 52.3083, "lon": -114.0972, "twp": 39, "rge": 1, "mer": "5th"},
    "Canmore": {"lat": 51.0892, "lon": -115.3580, "twp": 25, "rge": 10, "mer": "5th"},
    "Chestermere": {"lat": 51.0503, "lon": -113.8236, "twp": 24, "rge": 28, "mer": "4th"},
    "Strathmore": {"lat": 51.0367, "lon": -113.3978, "twp": 24, "rge": 25, "mer": "4th"},
    "Beaumont": {"lat": 53.3572, "lon": -113.4147, "twp": 51, "rge": 24, "mer": "4th"},
    "Stony Plain": {"lat": 53.5264, "lon": -114.0069, "twp": 53, "rge": 1, "mer": "5th"},
    "Fort Saskatchewan": {"lat": 53.7111, "lon": -113.2178, "twp": 55, "rge": 22, "mer": "4th"},
    "Drumheller": {"lat": 51.4631, "lon": -112.7086, "twp": 28, "rge": 19, "mer": "4th"},
    "Banff": {"lat": 51.1784, "lon": -115.5708, "twp": 25, "rge": 12, "mer": "5th"},
    "Jasper": {"lat": 52.8737, "lon": -118.0814, "twp": 46, "rge": 1, "mer": "6th"},
    "Hinton": {"lat": 53.4047, "lon": -117.5850, "twp": 52, "rge": 24, "mer": "5th"},
    "Whitecourt": {"lat": 54.1428, "lon": -115.6833, "twp": 60, "rge": 13, "mer": "5th"},
    "Slave Lake": {"lat": 55.2817, "lon": -114.7728, "twp": 74, "rge": 10, "mer": "5th"},
    "Peace River": {"lat": 56.2297, "lon": -117.2919, "twp": 82, "rge": 22, "mer": "5th"},
}


def search_alberta_location(query):
    """Search Alberta location database with fuzzy matching."""
    query = query.strip().lower()
    for place, data in ALBERTA_LOCATIONS.items():
        if place.lower() == query:
            return {
                "place_name": place,
                "latitude": data["lat"],
                "longitude": data["lon"],
                "township": data.get("twp"),
                "range": data.get("rge"),
                "meridian": data.get("mer"),
                "source": "database",
            }
    for place, data in ALBERTA_LOCATIONS.items():
        if query in place.lower():
            return {
                "place_name": place,
                "latitude": data["lat"],
                "longitude": data["lon"],
                "township": data.get("twp"),
                "range": data.get("rge"),
                "meridian": data.get("mer"),
                "source": "database",
            }
    return None


def location_search_api(request):
    """AJAX endpoint for location search autocomplete."""
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    results = []
    query_lower = query.lower()
    for place, data in ALBERTA_LOCATIONS.items():
        if query_lower in place.lower():
            results.append(
                {
                    "name": place,
                    "display": f"{place} (Twp {data.get('twp')}, Rge {data.get('rge')}, {data.get('mer')} Meridian)",
                    "latitude": data["lat"],
                    "longitude": data["lon"],
                    "township": data.get("twp"),
                    "range": data.get("rge"),
                    "meridian": data.get("mer"),
                }
            )
    return JsonResponse({"results": results[:10]})


def get_coordinates_from_township(township, range_val, meridian="4th"):
    """Convert Alberta township/range to approximate coordinates."""
    if meridian == "4th":
        base_lat = 49.0
        base_lon = -110.0
    elif meridian == "5th":
        base_lat = 49.0
        base_lon = -114.0
    elif meridian == "6th":
        base_lat = 49.0
        base_lon = -118.0
    else:
        base_lat = 49.0
        base_lon = -110.0

    lat_offset = (township - 1) * 0.087
    lon_offset = (range_val - 1) * 0.087
    latitude = base_lat + lat_offset
    longitude = base_lon - lon_offset
    return (latitude, longitude)

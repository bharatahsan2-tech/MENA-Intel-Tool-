"""Free global geocoding via Open-Meteo (no API key).

Turns a place name ("Bandar Abbas", "Al Azraq", "Manama") into coordinates and a
canonical country/region. LOCATION IS MANDATORY in every intel item, so this is
a first-class tool: the model resolves places named in strike/siren reports to
concrete coordinates for the analyst.
"""
# No deferred annotations: google-genai AFC needs runtime-evaluable signatures.
import requests

from ..config import HTTP_TIMEOUT, HTTP_USER_AGENT

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def geocode_place(place: str) -> dict:
    """Resolve a place name to coordinates and country/region.

    Use whenever a report names a location (city, base, port, island) so the
    intel item can carry precise coordinates.

    Args:
        place: A place name, e.g. "Bandar Abbas", "Manama", "Al Azraq Airbase".

    Returns:
        On success: {"found": True, "name", "lat", "lon", "country",
        "country_code", "admin1" (region), "timezone"}.
        On failure: {"found": False, "query": place, "error": <reason>}.
    """
    try:
        resp = requests.get(
            _GEOCODE_URL,
            params={"name": place, "count": 1, "language": "en", "format": "json"},
            headers={"User-Agent": HTTP_USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return {"found": False, "query": place, "error": "no match"}
        r = results[0]
        return {
            "found": True,
            "name": r.get("name"),
            "lat": r.get("latitude"),
            "lon": r.get("longitude"),
            "country": r.get("country"),
            "country_code": r.get("country_code"),
            "admin1": r.get("admin1"),
            "timezone": r.get("timezone"),
        }
    except requests.RequestException as e:
        return {"found": False, "query": place, "error": str(e)}


if __name__ == "__main__":
    import json
    print(json.dumps(geocode_place("Bandar Abbas"), indent=2))

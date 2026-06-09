"""NC OneMap AddressNC geocoder client."""

import json
import os
from typing import Any
from urllib.parse import urljoin

import requests

DEFAULT_GEOCODER_URL = (
    "https://services.nconemap.gov/secure/rest/services/"
    "AddressNC/AddressNC_geocoder/GeocodeServer"
)

# Rowan County approximate bounds (WGS84) to prefer local matches.
ROWAN_SEARCH_EXTENT = {
    "xmin": -80.73,
    "ymin": 35.45,
    "xmax": -80.38,
    "ymax": 35.82,
    "spatialReference": {"wkid": 4326},
}

DEFAULT_HEADERS = {"User-Agent": "RowanGISChatbot/1.0 (Rowan County GIS)"}


class GeocodeError(Exception):
    """Raised when geocoding fails."""


def get_geocoder_url() -> str:
    return os.getenv("NCONEMAP_GEOCODER_URL", DEFAULT_GEOCODER_URL).rstrip("/")


def geocode_address(
    address: str,
    *,
    max_locations: int = 5,
    min_score: float = 80.0,
) -> dict[str, Any] | None:
    """
    Geocode a single-line address using NC OneMap findAddressCandidates.
    Returns the best candidate or None if no confident match.
    """
    single_line = address.strip()
    if not single_line:
        return None

    if "nc" not in single_line.lower() and "north carolina" not in single_line.lower():
        single_line = f"{single_line}, Rowan County, NC"

    url = urljoin(get_geocoder_url() + "/", "findAddressCandidates")
    params = {
        "SingleLine": single_line,
        "outSR": 4326,
        "maxLocations": max_locations,
        "searchExtent": json.dumps(ROWAN_SEARCH_EXTENT),
        "f": "json",
    }

    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise GeocodeError(str(payload["error"]))

    candidates = payload.get("candidates") or []
    if not candidates:
        return None

    best = candidates[0]
    if float(best.get("score", 0)) < min_score:
        return None

    location = best.get("location") or {}
    return {
        "address": best.get("address"),
        "score": best.get("score"),
        "location": {"x": location.get("x"), "y": location.get("y")},
        "extent": best.get("extent"),
    }

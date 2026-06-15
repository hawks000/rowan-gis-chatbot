"""NC OneMap AddressNC geocoder client."""

import json
import os
from typing import Any
from urllib.parse import urljoin

import requests

from services.text_normalize import parse_address_parts, parse_city_from_query

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

ROWAN_COUNTY_NAME = "ROWAN"

DEFAULT_HEADERS = {"User-Agent": "RowanGISChatbot/1.0 (Rowan County GIS)"}


class GeocodeError(Exception):
    """Raised when geocoding fails."""


def get_geocoder_url() -> str:
    return os.getenv("NCONEMAP_GEOCODER_URL", DEFAULT_GEOCODER_URL).rstrip("/")


def _point_in_rowan_extent(x: float | None, y: float | None) -> bool:
    if x is None or y is None:
        return False
    return (
        ROWAN_SEARCH_EXTENT["xmin"] <= x <= ROWAN_SEARCH_EXTENT["xmax"]
        and ROWAN_SEARCH_EXTENT["ymin"] <= y <= ROWAN_SEARCH_EXTENT["ymax"]
    )


def _extent_in_rowan(extent: dict[str, Any] | None) -> bool:
    if not extent:
        return False
    center_x = (extent.get("xmin", 0) + extent.get("xmax", 0)) / 2
    center_y = (extent.get("ymin", 0) + extent.get("ymax", 0)) / 2
    return _point_in_rowan_extent(center_x, center_y)


def _is_rowan_candidate(candidate: dict[str, Any]) -> bool:
    """Keep only candidates that fall inside Rowan County."""
    location = candidate.get("location") or {}
    if not _point_in_rowan_extent(location.get("x"), location.get("y")):
        return False

    if not _extent_in_rowan(candidate.get("extent")):
        return False

    address = (candidate.get("address") or "").upper()
    if ROWAN_COUNTY_NAME in address:
        return True

    # AddressNC often omits county; accept in-bounds matches with a Rowan place name or ZIP.
    rowan_markers = (
        "SALISBURY",
        "CLEVELAND",
        "CHINA GROVE",
        "LANDIS",
        "SPENCER",
        "EAST SPENCER",
        "FAITH",
        "GRANITE QUARRY",
        "ROCKWELL",
        "WOODLEAF",
        "MOUNT ULLA",
        "MT ULLA",
        "BARBER",
        "GOLD HILL",
        ", NC",
    )
    return any(marker in address for marker in rowan_markers)


def geocode_address(
    address: str,
    *,
    max_locations: int = 5,
    min_score: float = 80.0,
) -> dict[str, Any] | None:
    """
    Geocode a single-line address using NC OneMap findAddressCandidates.
    Returns the best Rowan County candidate or None if no confident match.
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
    rowan_candidates = [
        candidate
        for candidate in candidates
        if float(candidate.get("score", 0)) >= min_score and _is_rowan_candidate(candidate)
    ]
    if not rowan_candidates:
        return None

    city = parse_city_from_query(single_line)

    def _rank(candidate: dict[str, Any]) -> float:
        score = float(candidate.get("score", 0))
        address = (candidate.get("address") or "").upper()
        if city and city in address:
            score += 15
        elif city and city not in address:
            score -= 25
        house_number, _ = parse_address_parts(single_line)
        if house_number:
            if address.startswith(f"{house_number} ") or f" {house_number} " in f" {address} ":
                score += 10
        return score

    rowan_candidates.sort(key=_rank, reverse=True)
    best = rowan_candidates[0]
    location = best.get("location") or {}
    return {
        "source": "nconemap",
        "address": best.get("address"),
        "score": best.get("score"),
        "location": {"x": location.get("x"), "y": location.get("y")},
        "extent": best.get("extent"),
    }

"""Unified Rowan County address geocoding."""

import logging

import requests

from services.nconemap_geocoder import GeocodeError, geocode_address
from services.rowan_address_search import search_rowan_address
from services.text_normalize import parse_address_parts, parse_city_from_query

logger = logging.getLogger(__name__)


def _geocode_matches_city(result: dict, city: str | None) -> bool:
    if not city:
        return True
    address = (result.get("address") or "").upper()
    if city in address:
        return True
    attrs = result.get("attributes") or {}
    comm = (attrs.get("COMM") or "").upper()
    return comm.startswith(city[:4])


def _pick_best_geocode(address: str, *candidates: dict | None) -> dict | None:
    city = parse_city_from_query(address)
    best: dict | None = None
    best_score = -1.0

    for candidate in candidates:
        if not candidate:
            continue
        score = float(candidate.get("score") or 0)
        if candidate.get("source") == "rowan_addressing" and score <= 1:
            score *= 100
        if _geocode_matches_city(candidate, city):
            score += 25
        if score > best_score:
            best = candidate
            best_score = score

    return best


def geocode_rowan_address(address: str) -> dict | None:
    """
    Geocode an address within Rowan County.

    Prefers Rowan addressing points for numbered street addresses, then NC AddressNC.
    """
    house_number, _ = parse_address_parts(address)
    rowan_result = None
    nconemap_result = None

    if house_number:
        try:
            rowan_result = search_rowan_address(address)
        except requests.RequestException as exc:
            logger.warning("Rowan address search failed: %s", exc)

    try:
        nconemap_result = geocode_address(address)
    except GeocodeError as exc:
        logger.warning("NC AddressNC geocoder error: %s", exc)

    picked = _pick_best_geocode(address, rowan_result, nconemap_result)
    if picked:
        return picked

    return rowan_result or nconemap_result

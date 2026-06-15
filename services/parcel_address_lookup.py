"""Match tax parcels by street address (house number, street, city)."""

from __future__ import annotations

import re
from typing import Any

from services.arcgis_client import query_layer
from services.text_normalize import fuzzy_score, parse_address_parts, parse_city_from_query


def _walk_coordinates(coords, visit) -> None:
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        visit(coords[0], coords[1])
        return
    for part in coords:
        _walk_coordinates(part, visit)


def _feature_centroid(feature: dict[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    if not coords:
        return None

    xs: list[float] = []
    ys: list[float] = []

    def collect(x: float, y: float) -> None:
        xs.append(x)
        ys.append(y)

    _walk_coordinates(coords, collect)
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def geocode_from_parcel_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    """Build a geocode result from a matched tax parcel feature."""
    props = feature.get("properties") or {}
    address = (props.get("PROP_ADDRESS") or props.get("TAXADD1") or "").strip()
    city = (props.get("CITY") or "").strip()
    label = f"{address}, {city}".strip(", ")
    if not label:
        return None

    centroid = _feature_centroid(feature)
    if not centroid:
        return None

    return {
        "source": "tax_parcel",
        "address": label,
        "score": 100,
        "location": {"x": centroid[0], "y": centroid[1]},
    }


def _parcel_house_number(props: dict[str, Any]) -> str | None:
    address = (props.get("PROP_ADDRESS") or props.get("TAXADD1") or "").strip()
    match = re.match(r"^(\d+)", address)
    return match.group(1) if match else None


def _score_parcel_feature(feature: dict[str, Any], *, query: str, city: str | None) -> float:
    props = feature.get("properties") or {}
    candidate = props.get("PROP_ADDRESS") or props.get("TAXADD1") or ""
    score = fuzzy_score(query, candidate)

    house_number, street_tokens = parse_address_parts(query)
    parcel_house = _parcel_house_number(props)
    if house_number and parcel_house:
        if parcel_house == house_number:
            score += 0.45
        else:
            score -= 0.55

    candidate_upper = candidate.upper()
    if house_number and house_number in candidate_upper.split():
        score += 0.15
    if street_tokens and all(token in candidate_upper for token in street_tokens[:2]):
        score += 0.15

    parcel_city = (props.get("CITY") or "").upper()
    if city and parcel_city:
        if city in parcel_city or parcel_city.startswith(city[:4]):
            score += 0.25
        elif city not in parcel_city:
            score -= 0.2

    return score


def _build_parcel_where(address: str) -> str | None:
    house_number, street_tokens = parse_address_parts(address)
    if not house_number:
        return None

    clauses = [
        f"(PROP_ADDRESS LIKE '{house_number} %' OR TAXADD1 LIKE '{house_number} %')",
    ]
    for token in street_tokens[:3]:
        escaped = token.replace("'", "''")
        clauses.append(f"UPPER(PROP_ADDRESS) LIKE '%{escaped}%'")

    city = parse_city_from_query(address)
    if city:
        escaped_city = city.replace("'", "''")
        clauses.append(f"UPPER(CITY) LIKE '%{escaped_city[:5]}%'")

    return " AND ".join(clauses)


def lookup_parcel_by_address(address: str) -> dict[str, Any] | None:
    """
    Find tax parcel(s) by address attributes.

    Returns a GeoJSON FeatureCollection with the best-scored parcel first,
    or None when no reasonable match exists.
    """
    where = _build_parcel_where(address)
    if not where:
        return None

    geojson = query_layer(where, result_record_count=15)
    features = geojson.get("features") or []
    if not features:
        return None

    city = parse_city_from_query(address)
    scored = [
        (_score_parcel_feature(feature, query=address, city=city), feature)
        for feature in features
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_feature = scored[0]
    if best_score < 0.55:
        return None

    return {"type": "FeatureCollection", "features": [best_feature]}

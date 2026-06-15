"""Rowan County local address search via Public/search addressing points."""

import os
import re
from typing import Any
from urllib.parse import urljoin

import requests

from services.arcgis_client import DEFAULT_HEADERS, get_base_url
from services.text_normalize import fuzzy_score, parse_address_parts, strip_question_wrapper

DEFAULT_ADDRESS_LAYER = "Public/search/MapServer/0"


def get_address_layer_path() -> str:
    return os.getenv("ROWAN_ADDRESS_LAYER_URL", DEFAULT_ADDRESS_LAYER).lstrip("/")


def _layer_url() -> str:
    return f"{get_base_url()}/{get_address_layer_path()}"


def _query_addresses(where: str, *, limit: int = 10) -> list[dict[str, Any]]:
    url = urljoin(_layer_url() + "/", "query")
    params = {
        "where": where,
        "outFields": "Address,B_ADDRESS,ROAD_NAME,ROAD_TYPE,COMM,ZIPNUM",
        "returnGeometry": "true",
        "f": "json",
        "outSR": 4326,
        "resultRecordCount": limit,
    }

    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("features") or []


def _feature_to_result(feature: dict[str, Any], *, query: str, score: float) -> dict[str, Any]:
    geometry = feature.get("geometry") or {}
    attributes = feature.get("attributes") or {}
    return {
        "source": "rowan_addressing",
        "address": attributes.get("Address") or attributes.get("B_ADDRESS") or query,
        "score": round(score, 1),
        "location": {"x": geometry["x"], "y": geometry["y"]},
        "attributes": attributes,
    }


def _build_strict_where(house_number: str | None, street_tokens: list[str]) -> str | None:
    if not street_tokens and not house_number:
        return None

    clauses: list[str] = []
    if house_number:
        clauses.append(f"(Address LIKE '{house_number} %' OR B_ADDRESS = '{house_number}')")
    for token in street_tokens:
        escaped = token.replace("'", "''")
        clauses.append(f"UPPER(Address) LIKE '%{escaped}%'")
    return " AND ".join(clauses)


def _build_relaxed_where(house_number: str | None, street_tokens: list[str]) -> str | None:
    if not street_tokens and not house_number:
        return None

    if house_number and street_tokens:
        street_clause = " OR ".join(
            f"UPPER(Address) LIKE '%{token.replace(chr(39), chr(39)*2)}%'" for token in street_tokens
        )
        return f"(Address LIKE '{house_number} %' OR B_ADDRESS = '{house_number}') AND ({street_clause})"

    if house_number:
        return f"(Address LIKE '{house_number} %' OR B_ADDRESS = '{house_number}')"

    street_clause = " OR ".join(
        f"UPPER(Address) LIKE '%{token.replace(chr(39), chr(39)*2)}%'" for token in street_tokens
    )
    return f"({street_clause})"


def _pick_best_feature(features: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    if not features:
        return None

    scored: list[tuple[float, dict[str, Any]]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if "x" not in geometry or "y" not in geometry:
            continue
        attrs = feature.get("attributes") or {}
        candidate = attrs.get("Address") or attrs.get("B_ADDRESS") or ""
        score = fuzzy_score(query, candidate)
        house_number, _ = parse_address_parts(query)
        if house_number and str(attrs.get("B_ADDRESS") or "").startswith(house_number):
            score += 0.15
        scored.append((score, feature))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_feature = scored[0]
    if best_score < 0.55:
        return None
    return best_feature


def search_rowan_address(address: str, *, limit: int = 5) -> dict[str, Any] | None:
    """
    Search Rowan County addressing points (Public/search layer 0).
    Uses strict token matching first, then relaxed and fuzzy scoring.
    """
    subject = strip_question_wrapper(address)
    house_number, street_tokens = parse_address_parts(subject)
    if not house_number and not street_tokens:
        return None

    query_label = subject

    for builder in (_build_strict_where, _build_relaxed_where):
        where = builder(house_number, street_tokens)
        if not where:
            continue
        features = _query_addresses(where, limit=limit)
        best = _pick_best_feature(features, query_label)
        if best:
            attrs = best.get("attributes") or {}
            candidate = attrs.get("Address") or attrs.get("B_ADDRESS") or query_label
            return _feature_to_result(best, query=query_label, score=fuzzy_score(query_label, candidate))

    if house_number:
        features = _query_addresses(
            f"(Address LIKE '{house_number} %' OR B_ADDRESS = '{house_number}')",
            limit=max(limit, 15),
        )
        best = _pick_best_feature(features, query_label)
        if best:
            attrs = best.get("attributes") or {}
            candidate = attrs.get("Address") or attrs.get("B_ADDRESS") or query_label
            return _feature_to_result(best, query=query_label, score=fuzzy_score(query_label, candidate))

    return None

#!/usr/bin/env python3
"""
Rowan County GIS Chatbot
Public-facing chat interface for querying county GIS parcel data.
"""

import csv
import io
import logging
import os
import re
import time
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import create_auth_manager
from services.arcgis_client import (
    ArcGISQueryError,
    get_layer_catalog,
    query_layer,
    query_layer_at_point,
    summarize_features,
)
from services.address_geocode import geocode_rowan_address
from services.rowan_address_search import search_rowan_address
from services.chat_log import get_summary_stats, init_db, list_queries, log_query, set_needs_feature
from services.nconemap_geocoder import GeocodeError
from services.parcel_address_lookup import geocode_from_parcel_feature, lookup_parcel_by_address
from services.owner_search import build_owner_where_clause, combined_owner_name, owner_matches_query
from services.parcel_facts import format_parcel_attribute_lines
from services.parcel_report import format_property_details
from services.rod_links import format_deed_record_line, format_plat_record_line
from services.query_parser import QueryIntent, build_where_clause, parse_query, retry_intent_from_message
from services.text_normalize import fuzzy_score, parse_address_parts, parse_city_from_query
from services.rate_limit import is_rate_limited
from services.search_layers import (
    addresses_in_subdivision,
    canonical_subdivision_name,
    count_addresses_on_street,
    enrich_parcel_report_context,
    get_subdivision_catalog,
    identify_parcel_report_at_point,
    list_approved_subdivisions,
    parcels_in_subdivision,
    parcels_and_addresses_in_subdivision,
    query_parcel_report_by_polygon,
    search_street_centerlines,
    search_subdivision,
    suggest_subdivision_name,
)

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-fallback-key-change-in-production")

auth_manager = create_auth_manager()
auth_enabled = os.getenv("AUTH_ENABLED", "False").lower() == "true"
admin_auth_enabled = os.getenv("ADMIN_AUTH_ENABLED", "True").lower() == "true"

ALLOWED_TENANT_ID = os.getenv("ALLOWED_TENANT_ID", "977b42ab-7737-4552-86e7-b09ed296213d")
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "@rowancountync.gov")
AUTH_MODE = os.getenv("AUTH_MODE", "allowlist")
ALLOWED_USERS = [
    email.strip().lower()
    for email in os.getenv("ALLOWED_USERS", "").split(",")
    if email.strip()
]

ARCGIS_BASEMAP_URL = os.getenv(
    "ARCGIS_BASEMAP_URL",
    "https://gis.rowancountync.gov/arcgis/rest/services/Public/MapViewer/MapServer",
)
ARCGIS_PICTOMETRY_URL = os.getenv(
    "ARCGIS_PICTOMETRY_URL",
    "https://gis.rowancountync.gov/arcgis/rest/services/Pictometry/Pictometry2025/MapServer",
)
ARCGIS_WEBMAP_ITEM_ID = os.getenv("ARCGIS_WEBMAP_ITEM_ID", "").strip()
ARCGIS_PORTAL_URL = os.getenv(
    "ARCGIS_PORTAL_URL",
    "https://www.arcgis.com",
).rstrip("/")

logger.info(
    "GIS Chatbot starting - AUTH_ENABLED: %s, ADMIN_AUTH_ENABLED: %s",
    auth_enabled,
    admin_auth_enabled,
)

init_db()


def is_user_authorized(id_token_claims):
    """Check if user is authorized based on AUTH_MODE."""
    if not id_token_claims:
        return False

    tenant_id = id_token_claims.get("tid", "")
    user_email = id_token_claims.get("preferred_username", "Unknown")
    user_name = id_token_claims.get("name", "Unknown")

    if tenant_id != ALLOWED_TENANT_ID:
        logger.warning("SECURITY: Unauthorized tenant - %s", tenant_id)
        return False

    if AUTH_MODE == "allowlist":
        if not ALLOWED_USERS:
            logger.warning("SECURITY: allowlist empty - denying admin access")
            return False
        if user_email.lower() not in ALLOWED_USERS:
            logger.warning("SECURITY: User not in allowlist - %s", user_email)
            return False
    elif not user_email.endswith(ALLOWED_EMAIL_DOMAIN):
        logger.warning("SECURITY: Invalid email domain - %s", user_email)
        return False

    logger.info("AUTHORIZED admin: %s (%s)", user_name, user_email)
    return True


def require_admin_auth(f):
    """Require Azure AD auth for staff admin pages (independent of public AUTH_ENABLED)."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not admin_auth_enabled:
            return f(*args, **kwargs)

        if "admin_user" not in session or not session.get("admin_authorized", False):
            session["post_login_redirect"] = request.path
            return redirect(url_for("admin_login"))

        return f(*args, **kwargs)

    return decorated_function


def _geocode_for_address_query(address: str) -> dict | None:
    """Geocode an address, preferring Rowan points when the query names a city."""
    geocode = None
    try:
        geocode = geocode_rowan_address(address)
    except GeocodeError as exc:
        logger.warning("Address geocode error: %s", exc)

    city = parse_city_from_query(address)
    if city:
        try:
            rowan = search_rowan_address(address)
        except requests.RequestException as exc:
            logger.warning("Rowan address search failed: %s", exc)
            rowan = None
        if rowan:
            rowan_city = (rowan.get("attributes") or {}).get("COMM", "")
            rowan_address = (rowan.get("address") or "").upper()
            if city in rowan_address or rowan_city.startswith(city[:4]):
                return rowan

    return geocode


def _geocode_source_label(geocode: dict | None) -> str:
    if not geocode:
        return ""
    if geocode.get("source") == "map_click":
        return "Map click"
    if geocode.get("source") == "tax_parcel":
        return "Rowan County tax parcel records"
    if geocode.get("source") == "rowan_addressing":
        return "Rowan County addressing points"
    if geocode.get("source") == "nconemap":
        return "NC AddressNC geocoder (Rowan County)"
    return "geocoder"


def _location_line(row: dict) -> str:
    address = row.get("PROP_ADDRESS") or row.get("TAXADD1") or "No address on file"
    city = row.get("CITY") or ""
    return f"{address}, {city}".strip(", ")


def _resolve_geocode_for_parcel(
    geojson: dict,
    *,
    address_query: str,
) -> dict | None:
    """Prefer geocoding that matches the tax parcel we already found."""
    features = geojson.get("features") or []
    if len(features) == 1:
        parcel_geocode = geocode_from_parcel_feature(features[0])
        if parcel_geocode:
            return parcel_geocode

    geocode = _geocode_for_address_query(address_query)
    if len(features) == 1 and geocode:
        row = (features[0].get("properties") or {})
        parcel_label = _location_line(row).upper()
        geocode_label = (geocode.get("address") or "").upper()
        city = parse_city_from_query(address_query)
        if city and city not in geocode_label and city in parcel_label:
            parcel_geocode = geocode_from_parcel_feature(features[0])
            if parcel_geocode:
                return parcel_geocode

    return geocode


def _with_address_point(geojson: dict, geocode: dict | None) -> dict:
    """Include the addressing point in GeoJSON so the map can zoom like parcel results."""
    if not geocode or not geocode.get("location"):
        return geojson

    location = geocode["location"]
    point_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [location["x"], location["y"]],
        },
        "properties": {
            "Address": geocode.get("address") or "",
            "_lookup": "address_point",
        },
    }
    features = [point_feature, *(geojson.get("features") or [])]
    return {**geojson, "features": features}


def _walk_geojson_coordinates(coords, visit) -> None:
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        visit(coords[0], coords[1])
        return
    for part in coords:
        _walk_geojson_coordinates(part, visit)


def _scale_for_span(span: float) -> int:
    if span > 0.05:
        return 50000
    if span > 0.01:
        return 12000
    if span > 0.003:
        return 6000
    if span > 0.001:
        return 3000
    return 1800


def _build_map_target(geojson: dict, geocode: dict | None = None) -> dict | None:
    """Compute a WGS84 center/extent the map can zoom to reliably."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")

    def add_point(x: float, y: float) -> None:
        nonlocal xmin, ymin, xmax, ymax
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return
        xmin = min(xmin, x)
        ymin = min(ymin, y)
        xmax = max(xmax, x)
        ymax = max(ymax, y)

    for feature in geojson.get("features") or []:
        props = feature.get("properties") or {}
        if props.get("_lookup") == "address_point":
            continue
        geometry = feature.get("geometry") or {}
        _walk_geojson_coordinates(geometry.get("coordinates"), add_point)

    if geocode and geocode.get("location"):
        location = geocode["location"]
        add_point(location.get("x"), location.get("y"))

    if not (xmin < float("inf")):
        return None

    span = max(xmax - xmin, ymax - ymin)
    center = {"x": (xmin + xmax) / 2, "y": (ymin + ymax) / 2}
    return {
        "center": center,
        "extent": {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "wkid": 4326,
        },
        "scale": _scale_for_span(span),
    }


def _fetch_parcel_report(geojson: dict, geocode: dict | None) -> dict | None:
    """Query ParcelReport context using the parcel polygon when available."""
    from services.search_layers import _parcel_polygon_features

    parcel_features = _parcel_polygon_features(geojson)
    report = None

    if len(parcel_features) == 1:
        geometry = parcel_features[0].get("geometry") or {}
        if geometry.get("type") in {"Polygon", "MultiPolygon"}:
            try:
                polygon_report = query_parcel_report_by_polygon(geometry)
                if polygon_report.get("results"):
                    report = polygon_report
            except requests.RequestException as exc:
                logger.warning("ParcelReport polygon query failed: %s", exc)

    if not report and geocode and geocode.get("location"):
        location = geocode["location"]
        try:
            report = identify_parcel_report_at_point(location["x"], location["y"])
        except requests.RequestException as exc:
            logger.warning("ParcelReport identify failed: %s", exc)

    if not report and len(parcel_features) == 1:
        geometry = parcel_features[0].get("geometry") or {}
        if geometry.get("type") == "Polygon" and geometry.get("coordinates"):
            ring = geometry["coordinates"][0]
            xs = [point[0] for point in ring]
            ys = [point[1] for point in ring]
            try:
                report = identify_parcel_report_at_point((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            except requests.RequestException as exc:
                logger.warning("ParcelReport identify failed: %s", exc)

    if not parcel_features:
        return report

    report = enrich_parcel_report_context(report, geojson)
    return report


def _fuzzy_owner_search(name: str) -> dict:
    """Broaden owner search when exact token matching finds nothing."""
    from services.owner_search import extract_owner_tokens

    tokens = extract_owner_tokens(name)
    if not tokens:
        return {"type": "FeatureCollection", "features": []}

    longest = max(tokens, key=len).replace("'", "''")
    broad_clauses = [
        f"UPPER(OWNNAME) LIKE '%{longest}%'",
        f"UPPER(OWN2) LIKE '%{longest}%'",
    ]
    broad = query_layer(
        f"({' OR '.join(broad_clauses)})",
        result_record_count=75,
    )
    matched = []
    for feature in broad.get("features") or []:
        props = feature.get("properties") or {}
        if owner_matches_query(name, props):
            matched.append(feature)

    if matched:
        return {"type": "FeatureCollection", "features": matched}

    try:
        return query_layer(build_owner_where_clause(name), result_record_count=50)
    except ArcGISQueryError:
        return {"type": "FeatureCollection", "features": []}


def _narrow_parcel_results(
    geojson: dict,
    *,
    query: str,
    geocode: dict | None = None,
) -> dict:
    """When a geocoded point hits multiple parcels, keep the best address match."""
    features = geojson.get("features") or []
    if len(features) <= 1:
        return geojson

    compare_text = (geocode or {}).get("address") or query
    house_number, street_tokens = parse_address_parts(query)
    city = parse_city_from_query(query)
    scored: list[tuple[float, dict]] = []

    for feature in features:
        props = feature.get("properties") or {}
        candidate = props.get("PROP_ADDRESS") or props.get("TAXADD1") or ""
        score = fuzzy_score(compare_text, candidate)
        candidate_upper = candidate.upper()

        parcel_house_match = re.match(r"^(\d+)", candidate.strip())
        parcel_house = parcel_house_match.group(1) if parcel_house_match else None
        if house_number and parcel_house:
            if parcel_house == house_number:
                score += 0.45
            else:
                score -= 0.55

        if house_number and house_number in candidate_upper.split():
            score += 0.2
        if street_tokens and all(token in candidate_upper for token in street_tokens[:2]):
            score += 0.15
        if city:
            parcel_city = (props.get("CITY") or "").upper()
            if city in parcel_city or parcel_city.startswith(city[:4]):
                score += 0.25
            elif parcel_city:
                score -= 0.2

        scored.append((score, feature))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.55:
        return {"type": "FeatureCollection", "features": [scored[0][1]]}
    return geojson


def _client_ip() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "unknown")


def _rate_limit_or_response(session_id: str):
    limited, retry_after = is_rate_limited(_client_ip(), session_id or "anonymous")
    if not limited:
        return None
    return jsonify(
        {
            "status": "rate_limited",
            "message": f"Too many requests. Please wait {retry_after} seconds and try again.",
            "retry_after": retry_after,
        }
    ), 429


def _split_fact_line(line: str) -> tuple[str, str]:
    if ":" in line:
        label, value = line.split(":", 1)
        return label.strip(), value.strip()
    return "Info", line.strip()


def _build_property_card(summary: dict | None, parcel_report: dict | None = None) -> dict | None:
    if not summary:
        return None
    pin = summary.get("PIN") or summary.get("PARCEL_ID")
    if not pin:
        return None

    facts = [_split_fact_line(line) for line in format_parcel_attribute_lines(summary)]
    facts = [{"label": label, "value": value} for label, value in facts if value]

    context = []
    if parcel_report:
        for line in format_property_details(parcel_report, parcel_pin=str(pin)):
            label, value = _split_fact_line(line)
            if value:
                context.append({"label": label, "value": value})

    return {
        "pin": str(pin),
        "owner": combined_owner_name(summary),
        "address": summary.get("PROP_ADDRESS") or summary.get("TAXADD1") or summary.get("Address") or "",
        "facts": facts,
        "context": context[:10],
    }


def _build_query_suggestions(intent: QueryIntent, result_count: int) -> list[dict[str, str]]:
    if result_count > 0:
        return []

    suggestions: list[dict[str, str]] = []
    if intent.intent_type.startswith("subdivision"):
        suggestion = suggest_subdivision_name(intent.value)
        if suggestion and suggestion.upper() != intent.value.upper():
            suggestions.append(
                {
                    "label": suggestion,
                    "query": f"how many parcels in {suggestion}",
                }
            )
    return suggestions


def _format_summary_message(intent, summaries, result_count, geocode=None, parcel_report=None):
    source_label = _geocode_source_label(geocode)

    def _fact_lines(row: dict, parcel_report_data=None) -> list[str]:
        lines = format_parcel_attribute_lines(row)
        if any(line.startswith("ZIP:") for line in lines):
            return lines
        if not parcel_report_data:
            return lines
        for item in (parcel_report_data.get("results") or []):
            if item.get("layerName") != "ZIP Code":
                continue
            zip_code = (item.get("attributes") or {}).get("ZIP_CODE")
            if zip_code:
                lines.append(f"ZIP: {zip_code}")
            break
        return lines

    def _deed_line(row: dict) -> str:
        return format_deed_record_line(row.get("DEEDBOOK"), row.get("DEEDPAGE"))

    def _plat_line(row: dict) -> str:
        return format_plat_record_line(row.get("PLATBOOK"), row.get("PLATPAGE"))

    def _record_lines(row: dict) -> list[str]:
        return [_deed_line(row), _plat_line(row)]

    def _detail_block(*, focus: str = "", parcel_pin: str | None = None) -> list[str]:
        lines = format_property_details(parcel_report, focus=focus, parcel_pin=parcel_pin)
        if not lines:
            return []
        heading = "Answer" if focus else "Property details"
        block = [heading + ":"]
        block.extend(f"• {line}" for line in lines)
        return block

    def _join(blocks: list[str]) -> str:
        return "\n".join(block for block in blocks if block)

    if intent.intent_type == "street_houses":
        street = intent.value
        if result_count == 0:
            return f"No addresses found on {street}."
        label = "address" if result_count == 1 else "addresses"
        return (
            f"Found {result_count} {label} on {street} "
            f"(Rowan addressing points matched via street centerline Whole_Name)."
        )

    if intent.intent_type == "street_parcels":
        street = intent.value
        if result_count == 0:
            return f"No parcels found on {street}."
        if result_count == 1:
            row = summaries[0]
            pin = row.get("PIN") or row.get("PARCEL_ID") or "Unknown"
            owner = combined_owner_name(row)
            return f"Found 1 parcel on {street}: {pin} — {owner}."
        return f"Found {result_count} parcels on {street}."

    if intent.intent_type == "list_subdivisions":
        if result_count == 0:
            return "No approved major subdivisions were found in Rowan County GIS."
        names = sorted({
            str((row.get("SubName") or row.get("SUBNAME") or "")).strip()
            for row in summaries
            if str((row.get("SubName") or row.get("SUBNAME") or "")).strip()
        })
        preview = "\n".join(f"• {name}" for name in names[:40])
        extra = f"\n… and {len(names) - 40} more." if len(names) > 40 else ""
        return (
            f"Rowan County has {len(names)} approved major subdivisions.\n"
            f"{preview}{extra}"
        )

    if intent.intent_type == "subdivision_addresses":
        sub = intent.value
        if result_count == 0:
            if suggest_subdivision_name(sub):
                return f'No addresses found for "{sub}".'
            return f'No subdivision matching "{sub}" was found.'
        label = "address" if result_count == 1 else "addresses"
        return f"Found {result_count} {label} in subdivision {sub}."

    if intent.intent_type == "subdivision_both":
        sub = intent.value
        if result_count == 0:
            if suggest_subdivision_name(sub):
                return f'No results for "{sub}".'
            return f'No subdivision matching "{sub}" was found.'
        return (
            f"Found {result_count} parcels and/or addresses in subdivision {sub}. "
            "Green polygons are tax parcels; blue markers are address points."
        )

    if intent.intent_type == "subdivision_parcels":
        sub = intent.value
        if result_count == 0:
            if suggest_subdivision_name(sub):
                return f'No parcels found for "{sub}".'
            return f'No subdivision matching "{sub}" was found.'
        if result_count == 1:
            row = summaries[0]
            pin = row.get("PIN") or row.get("PARCEL_ID") or "Unknown"
            owner = combined_owner_name(row)
            return f"Found 1 parcel in {sub}: {pin} — {owner}."
        owners = sorted({combined_owner_name(row) for row in summaries if combined_owner_name(row) != "Unknown owner"})
        owner_preview = ", ".join(owners[:5])
        if len(owners) > 5:
            owner_preview += f", and {len(owners) - 5} more"
        return (
            f"Found {result_count} parcels in subdivision {sub}. "
            f"Owners include: {owner_preview}."
        )

    if result_count == 0:
        if geocode and geocode.get("address"):
            return (
                f"{source_label} matched '{intent.value}' to {geocode['address']}, "
                "but no matching parcel polygon was found nearby."
            )
        return f"No parcels found for: {intent.description}."

    if result_count == 1:
        row = summaries[0]
        pin = row.get("PIN") or row.get("PARCEL_ID") or "Unknown"
        owner = combined_owner_name(row)
        location = _location_line(row)
        subject = location

        blocks = []
        if intent.context_focus:
            blocks.append(f"For {subject}:")
            blocks.extend(_detail_block(focus=intent.context_focus, parcel_pin=pin if pin != "Unknown" else None))
            blocks.append("")
            blocks.append(f"Parcel {pin}")
        elif intent.intent_type == "map_click":
            blocks.append(f"Map parcel {pin}")
        else:
            blocks.append(f"Found parcel {pin}")

        blocks.append(f"Owner: {owner}")
        blocks.append(f"Address: {location}")
        blocks.extend(_fact_lines(row, parcel_report))
        blocks.extend(_record_lines(row))

        if not intent.context_focus:
            detail_lines = _detail_block(parcel_pin=pin if pin != "Unknown" else None)
            if detail_lines:
                blocks.append("")
                blocks.extend(detail_lines)

        blocks.append("")
        if intent.intent_type == "map_click":
            blocks.append("Selected on the map.")
        else:
            blocks.append(f"Located via {source_label} at {location}.")

        return _join(blocks)

    geocode_note = ""
    if geocode and geocode.get("address"):
        geocode_note = f" Matched via {source_label}: {geocode['address']}."
    return f"Found {result_count} parcels matching: {intent.description}.{geocode_note}"


def _layer_used_for_intent(intent: QueryIntent) -> str:
    mapping = {
        "address": "Public/search/MapServer/0",
        "street_houses": "Public/search/MapServer/1 + 0",
        "street_parcels": "Public/RowanTaxParcels/MapServer/0",
        "subdivision_addresses": "Public/IntranetMap/MapServer/27 + search/0",
        "subdivision_parcels": "Public/IntranetMap/MapServer/27 + RowanTaxParcels",
        "subdivision_both": "Public/IntranetMap/MapServer/27 + RowanTaxParcels + search/0",
        "list_subdivisions": "Public/IntranetMap/MapServer/27",
    }
    return mapping.get(intent.intent_type, "RowanTaxParcels")


def _query_for_intent(intent: QueryIntent):
    """Route parsed intent to the correct GIS layer(s)."""
    geocode = None
    parcel_report = None
    overlay_geojson = None

    if intent.intent_type == "street_houses":
        geojson = count_addresses_on_street(intent.value)
        streets = search_street_centerlines(intent.value, limit=5)
        if streets.get("features"):
            overlay_geojson = streets
        return geojson, geocode, parcel_report, overlay_geojson

    if intent.intent_type == "list_subdivisions":
        geojson = list_approved_subdivisions()
        return geojson, geocode, parcel_report, overlay_geojson

    if intent.intent_type == "subdivision_addresses":
        subdivisions = search_subdivision(intent.value)
        intent.value = canonical_subdivision_name(subdivisions, intent.value)
        geojson = addresses_in_subdivision(intent.value)
        return geojson, geocode, parcel_report, subdivisions

    if intent.intent_type == "subdivision_both":
        subdivisions = search_subdivision(intent.value)
        intent.value = canonical_subdivision_name(subdivisions, intent.value)
        geojson = parcels_and_addresses_in_subdivision(intent.value)
        return geojson, geocode, parcel_report, subdivisions

    if intent.intent_type == "subdivision_parcels":
        subdivisions = search_subdivision(intent.value)
        intent.value = canonical_subdivision_name(subdivisions, intent.value)
        geojson = parcels_in_subdivision(intent.value)
        return geojson, geocode, parcel_report, subdivisions

    if intent.intent_type == "address":
        try:
            direct = lookup_parcel_by_address(intent.value)
        except requests.RequestException as exc:
            logger.warning("Direct parcel address lookup failed: %s", exc)
            direct = None

        if direct and direct.get("features"):
            geojson = direct
            geocode = _resolve_geocode_for_parcel(geojson, address_query=intent.value)
            try:
                parcel_report = _fetch_parcel_report(geojson, geocode)
            except requests.RequestException as exc:
                logger.warning("ParcelReport identify failed: %s", exc)
            return geojson, geocode, parcel_report, overlay_geojson

        try:
            geocode = _geocode_for_address_query(intent.value)
        except GeocodeError as exc:
            logger.warning("Address geocode error: %s", exc)
            geocode = None

        if geocode and geocode.get("location"):
            location = geocode["location"]
            geojson = query_layer_at_point(location["x"], location["y"])
            geojson = _narrow_parcel_results(geojson, query=intent.value, geocode=geocode)
            if geojson.get("features"):
                geocode = _resolve_geocode_for_parcel(geojson, address_query=intent.value)
                try:
                    parcel_report = _fetch_parcel_report(geojson, geocode)
                except requests.RequestException as exc:
                    logger.warning("ParcelReport identify failed: %s", exc)
                return geojson, geocode, parcel_report, overlay_geojson

        where = build_where_clause(intent)
        geojson = query_layer(where)
        return geojson, geocode, parcel_report, overlay_geojson

    where = build_where_clause(intent)
    geojson = query_layer(where)
    if intent.intent_type == "owner" and not geojson.get("features"):
        geojson = _fuzzy_owner_search(intent.value)
    if geojson.get("features") and len(geojson.get("features", [])) == 1:
        try:
            parcel_report = _fetch_parcel_report(geojson, geocode)
        except requests.RequestException as exc:
            logger.warning("ParcelReport identify failed: %s", exc)
    return geojson, geocode, parcel_report, overlay_geojson


def _query_parcels_for_intent(intent):
    """Backward-compatible wrapper."""
    geojson, geocode, _, _ = _query_for_intent(intent)
    return geojson, geocode


def _parcel_centroid_distance(feature: dict, longitude: float, latitude: float) -> float:
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    if not coords:
        return float("inf")

    points: list[tuple[float, float]] = []

    def collect(part) -> None:
        if not part:
            return
        if isinstance(part[0], (int, float)):
            points.append((float(part[0]), float(part[1])))
            return
        for item in part:
            collect(item)

    collect(coords)
    if not points:
        return float("inf")

    center_lon = sum(point[0] for point in points) / len(points)
    center_lat = sum(point[1] for point in points) / len(points)
    return (center_lon - longitude) ** 2 + (center_lat - latitude) ** 2


def _single_parcel_at_point(geojson: dict, longitude: float, latitude: float) -> dict:
    features = geojson.get("features") or []
    if len(features) <= 1:
        return geojson

    best = min(
        features,
        key=lambda feature: _parcel_centroid_distance(feature, longitude, latitude),
    )
    return {"type": "FeatureCollection", "features": [best]}


def _query_parcel_at_point(longitude: float, latitude: float) -> tuple[dict, dict | None, dict | None]:
    """Find the tax parcel at a map coordinate and enrich with ParcelReport context."""
    geocode = {
        "address": f"Map click ({latitude:.5f}, {longitude:.5f})",
        "location": {"x": longitude, "y": latitude},
        "source": "map_click",
        "score": 100,
    }
    geojson = query_layer_at_point(longitude, latitude, distance_feet=25)
    geojson = _single_parcel_at_point(geojson, longitude, latitude)

    parcel_report = None
    if len(geojson.get("features", [])) == 1:
        try:
            parcel_report = _fetch_parcel_report(geojson, geocode)
        except requests.RequestException as exc:
            logger.warning("ParcelReport identify failed for map click: %s", exc)

    return geojson, geocode, parcel_report


def _build_api_response(
    *,
    intent: QueryIntent,
    geojson: dict,
    geocode: dict | None,
    parcel_report: dict | None,
    overlay_geojson: dict | None,
    session_id: str,
    user_message: str,
    started: float,
) -> tuple[dict, int]:
    summaries = summarize_features(geojson)
    result_count = len(geojson.get("features", []))
    map_geojson = _with_address_point(geojson, geocode) if geocode else geojson
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    status = "success" if result_count else "no_results"

    log_query(
        session_id=session_id,
        user_message=user_message,
        parse_method="regex" if intent.intent_type != "map_click" else "map_click",
        intent_type=intent.intent_type,
        intent=intent.to_dict(),
        status=status,
        result_count=result_count,
        layer_used=_layer_used_for_intent(intent)
        if intent.intent_type != "map_click"
        else "RowanTaxParcels/map_click",
        response_ms=elapsed_ms,
    )

    message = _format_summary_message(intent, summaries, result_count, geocode, parcel_report)
    if result_count == 0 and intent.intent_type == "map_click":
        message = "No tax parcel was found at that map location. Try clicking closer to a parcel boundary."

    map_target = _build_map_target(map_geojson, geocode) if result_count else None
    property_card = None
    if result_count == 1 and summaries:
        property_card = _build_property_card(summaries[0], parcel_report)
    payload = {
        "status": status,
        "message": message,
        "intent": intent.to_dict(),
        "geojson": map_geojson,
        "overlay_geojson": overlay_geojson,
        "summaries": summaries,
        "result_count": result_count,
        "geocode": geocode,
        "parcel_report": parcel_report,
        "map_target": map_target,
        "suggestions": _build_query_suggestions(intent, result_count),
        "property_card": property_card,
    }
    http_status = 200 if result_count else 404
    return payload, http_status


@app.route("/login")
def login():
    """Legacy login route — redirects to admin login."""
    return redirect(url_for("admin_login"))


@app.route("/admin/login")
def admin_login():
    """Initiate Microsoft login for admin pages."""
    if not admin_auth_enabled:
        flash("Admin auth is disabled in this environment.", "info")
        return redirect(url_for("admin_queries"))

    if not auth_manager:
        flash("Authentication is not configured. Set Azure AD env vars.", "error")
        return render_template("error.html", error="Authentication system unavailable"), 500

    try:
        auth_result = auth_manager.build_auth_url()
        session["auth_state"] = auth_result["state"]
        session["auth_flow"] = "admin"
        return redirect(auth_result["auth_url"])
    except Exception as exc:
        logger.error("Admin login error: %s", exc)
        flash("Failed to initiate login.", "error")
        return render_template("error.html", error="Login initiation failed"), 500


@app.route("/getAToken")
def get_a_token():
    """Handle OAuth callback for admin authentication."""
    if not auth_manager:
        flash("Authentication system unavailable.", "error")
        return render_template("error.html", error="Authentication system unavailable"), 500

    try:
        authorization_code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        if error:
            flash(f"Authentication failed: {request.args.get('error_description', error)}", "error")
            return redirect(url_for("admin_login"))

        if not state or state != session.get("auth_state"):
            flash("Invalid authentication state.", "error")
            return redirect(url_for("admin_login"))

        session.pop("auth_state", None)

        if not authorization_code:
            flash("No authorization code received.", "error")
            return redirect(url_for("admin_login"))

        token_response = auth_manager.acquire_token_by_authorization_code(authorization_code, state)
        id_token_claims = token_response.get("id_token_claims", {})

        if not is_user_authorized(id_token_claims):
            flash("You are not authorized to access admin pages.", "error")
            return redirect(url_for("index"))

        session["admin_user"] = {
            "name": id_token_claims.get("name", "Unknown"),
            "email": id_token_claims.get("preferred_username", "Unknown"),
            "id": id_token_claims.get("oid", "Unknown"),
        }
        session["admin_authorized"] = True
        session.pop("auth_flow", None)

        redirect_target = session.pop("post_login_redirect", None) or url_for("admin_queries")
        flash(f"Welcome, {session['admin_user']['name']}!", "success")
        return redirect(redirect_target)

    except Exception as exc:
        logger.error("Auth callback error: %s", exc)
        flash("Authentication failed.", "error")
        return redirect(url_for("admin_login"))


@app.route("/admin/logout")
def admin_logout():
    """Log out admin user."""
    user_name = session.get("admin_user", {}).get("name", "Unknown")
    session.pop("admin_user", None)
    session.pop("admin_authorized", None)
    logger.info("Admin logged out: %s", user_name)
    flash("Signed out of admin.", "info")

    if auth_manager and admin_auth_enabled:
        try:
            return redirect(auth_manager.get_logout_url())
        except Exception as exc:
            logger.error("Logout URL error: %s", exc)

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    return redirect(url_for("admin_logout"))


@app.route("/")
def index():
    """Public GIS chatbot page."""
    return render_template(
        "chat.html",
        basemap_url=ARCGIS_BASEMAP_URL,
        pictometry_url=ARCGIS_PICTOMETRY_URL,
        webmap_item_id=ARCGIS_WEBMAP_ITEM_ID,
        portal_url=ARCGIS_PORTAL_URL,
    )


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "app": "rowan-gis-chatbot",
        "build": "2026.06.17-ux48",
    })


@app.route("/api/layers")
def api_layers():
    """Return queryable layer catalog and example prompts."""
    return jsonify({"layers": get_layer_catalog()})


@app.route("/api/subdivisions")
def api_subdivisions():
    """Return approved subdivision names for client-side autocomplete."""
    try:
        return jsonify({"names": get_subdivision_catalog()})
    except requests.RequestException as exc:
        logger.warning("Subdivision catalog failed: %s", exc)
        return jsonify({"names": []})


@app.route("/api/autocomplete")
def api_autocomplete():
    """Suggest subdivisions and streets matching partial input."""
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"subdivisions": [], "streets": []})

    needle = query.upper()
    subdivisions = [
        name for name in get_subdivision_catalog()
        if needle in name.upper()
    ][:8]

    streets: list[str] = []
    try:
        street_payload = search_street_centerlines(query, limit=8)
        for feature in street_payload.get("features") or []:
            props = feature.get("properties") or {}
            label = props.get("Whole_Name") or props.get("ROAD_NAME")
            if label and label not in streets:
                streets.append(str(label))
    except requests.RequestException as exc:
        logger.warning("Street autocomplete failed: %s", exc)

    return jsonify({"subdivisions": subdivisions, "streets": streets[:8]})


@app.route("/api/query", methods=["POST"])
def api_query():
    """Parse user message, query ArcGIS REST, log interaction."""
    started = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    session_id = (payload.get("session_id") or "anonymous").strip()[:64]

    rate_response = _rate_limit_or_response(session_id)
    if rate_response:
        return rate_response

    if not message:
        return jsonify({"error": "Message is required."}), 400

    intent = parse_query(message)
    if not intent:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="none",
            intent_type=None,
            intent=None,
            status="parse_failed",
            error_message="Could not understand the question.",
            response_ms=elapsed_ms,
        )
        return jsonify(
            {
                "status": "parse_failed",
                "message": (
                    "I couldn't understand that question yet. Try one of the suggested "
                    "examples, such as a PIN, street address, owner name, or street name."
                ),
                "geojson": {"type": "FeatureCollection", "features": []},
                "summaries": [],
                "result_count": 0,
            }
        ), 422

    try:
        geojson, geocode, parcel_report, overlay_geojson = _query_for_intent(intent)
        summaries = summarize_features(geojson)
        result_count = len(geojson.get("features", []))

        if result_count == 0:
            retry_intent = retry_intent_from_message(message, intent)
            if retry_intent:
                logger.info(
                    "Retrying query with extracted subject %r (was %r)",
                    retry_intent.value,
                    intent.value,
                )
                intent = retry_intent
                geojson, geocode, parcel_report, overlay_geojson = _query_for_intent(intent)
                summaries = summarize_features(geojson)
                result_count = len(geojson.get("features", []))

        map_geojson = geojson
        if geocode:
            map_geojson = _with_address_point(geojson, geocode)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        status = "success" if result_count else "no_results"
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="regex",
            intent_type=intent.intent_type,
            intent=intent.to_dict(),
            status=status,
            result_count=result_count,
            layer_used=_layer_used_for_intent(intent),
            response_ms=elapsed_ms,
        )

        summary_message = _format_summary_message(
            intent, summaries, result_count, geocode, parcel_report
        )
        suggestions = _build_query_suggestions(intent, result_count)
        property_card = None
        if result_count == 1 and summaries:
            property_card = _build_property_card(summaries[0], parcel_report)
        map_target = _build_map_target(map_geojson, geocode) if result_count else None
        return jsonify(
            {
                "status": status,
                "message": summary_message,
                "intent": intent.to_dict(),
                "geojson": map_geojson,
                "overlay_geojson": overlay_geojson,
                "summaries": summaries,
                "result_count": result_count,
                "geocode": geocode,
                "parcel_report": parcel_report,
                "map_target": map_target,
                "suggestions": suggestions,
                "property_card": property_card,
            }
        )

    except (ArcGISQueryError, GeocodeError, requests.RequestException, ValueError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error("Query failed: %s", exc)
        log_query(
            session_id=session_id,
            user_message=message,
            parse_method="regex",
            intent_type=intent.intent_type,
            intent=intent.to_dict(),
            status="error",
            error_message=str(exc),
            response_ms=elapsed_ms,
        )
        return jsonify(
            {
                "status": "error",
                "message": "Something went wrong querying GIS data. Please try again.",
                "error": str(exc),
            }
        ), 502


@app.route("/api/parcel-at-point", methods=["POST"])
def api_parcel_at_point():
    """Look up the tax parcel at a map click coordinate."""
    started = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    session_id = (payload.get("session_id") or "anonymous").strip()[:64]

    rate_response = _rate_limit_or_response(session_id)
    if rate_response:
        return rate_response

    try:
        longitude = float(payload.get("longitude"))
        latitude = float(payload.get("latitude"))
    except (TypeError, ValueError):
        return jsonify({"error": "longitude and latitude are required numbers."}), 400

    if not (-84.5 <= longitude <= -79.0 and 33.0 <= latitude <= 37.5):
        return jsonify(
            {
                "status": "no_results",
                "message": "That map location is outside Rowan County.",
                "geojson": {"type": "FeatureCollection", "features": []},
                "summaries": [],
                "result_count": 0,
            }
        ), 404

    intent = QueryIntent(
        intent_type="map_click",
        field="PIN",
        value=f"{latitude:.5f},{longitude:.5f}",
        description=f"Parcel at map click ({latitude:.5f}, {longitude:.5f})",
    )
    user_message = f"Map click ({latitude:.5f}, {longitude:.5f})"

    try:
        geojson, geocode, parcel_report = _query_parcel_at_point(longitude, latitude)
        response_payload, http_status = _build_api_response(
            intent=intent,
            geojson=geojson,
            geocode=geocode,
            parcel_report=parcel_report,
            overlay_geojson=None,
            session_id=session_id,
            user_message=user_message,
            started=started,
        )
        return jsonify(response_payload), http_status
    except (ArcGISQueryError, requests.RequestException, ValueError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.error("Map click lookup failed: %s", exc)
        log_query(
            session_id=session_id,
            user_message=user_message,
            parse_method="map_click",
            intent_type="map_click",
            intent=intent.to_dict(),
            status="error",
            error_message=str(exc),
            response_ms=elapsed_ms,
        )
        return jsonify(
            {
                "status": "error",
                "message": "Something went wrong looking up that map location. Please try again.",
                "error": str(exc),
            }
        ), 502


@app.route("/admin/queries")
@require_admin_auth
def admin_queries():
    """Staff view of logged chat queries."""
    unmatched_only = request.args.get("unmatched") == "1"
    status_filter = request.args.get("status") or None
    entries = list_queries(status=status_filter, unmatched_only=unmatched_only, limit=300)
    stats = get_summary_stats()
    return render_template(
        "admin_queries.html",
        entries=entries,
        stats=stats,
        unmatched_only=unmatched_only,
        status_filter=status_filter or "",
        admin_user=session.get("admin_user", {}),
        admin_auth_enabled=admin_auth_enabled,
    )


@app.route("/admin/queries/export")
@require_admin_auth
def admin_queries_export():
    """Export query log as CSV for LLM tuning / feature planning."""
    unmatched_only = request.args.get("unmatched") == "1"
    entries = list_queries(unmatched_only=unmatched_only, limit=5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "created_at",
            "session_id",
            "user_message",
            "parse_method",
            "intent_type",
            "status",
            "result_count",
            "layer_used",
            "error_message",
            "response_ms",
            "needs_feature",
        ]
    )
    for row in entries:
        writer.writerow(
            [
                row.get("id"),
                row.get("created_at"),
                row.get("session_id"),
                row.get("user_message"),
                row.get("parse_method"),
                row.get("intent_type"),
                row.get("status"),
                row.get("result_count"),
                row.get("layer_used"),
                row.get("error_message"),
                row.get("response_ms"),
                row.get("needs_feature"),
            ]
        )

    filename = "gis-chatbot-queries.csv"
    return (
        output.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.route("/admin/queries/<int:entry_id>/flag", methods=["POST"])
@require_admin_auth
def admin_flag_query(entry_id):
    """Mark a logged query as needing a new feature."""
    payload = request.get_json(silent=True) or {}
    needs_feature = bool(payload.get("needs_feature", True))
    if set_needs_feature(entry_id, needs_feature):
        return jsonify({"ok": True, "needs_feature": needs_feature})
    return jsonify({"error": "Entry not found."}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    debug_mode = os.getenv("ENVIRONMENT", "production") == "development"
    app.run(debug=debug_mode, host="0.0.0.0", port=port, use_reloader=False)

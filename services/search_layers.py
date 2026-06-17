"""Rowan County Public/search and ParcelReport layer queries."""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

import requests

from services.arcgis_client import DEFAULT_HEADERS, get_base_url, get_parcel_layer_path
from services.text_normalize import (
    fuzzy_best_match,
    normalize_subdivision_query,
    subdivision_search_tokens,
)

SEARCH_ADDRESS_LAYER = "Public/search/MapServer/0"
SEARCH_STREET_LAYER = "Public/search/MapServer/1"
SEARCH_SUBDIVISION_LAYER = "Public/search/MapServer/3"
DEFAULT_APPROVED_SUBDIVISION_LAYER = "Public/IntranetMap/MapServer/27"
PARCEL_REPORT_SERVICE = "Public/ParcelReport/MapServer"
DEFAULT_FLOOD_PARCEL_LAYER = "Public/Open_Data_Downloads/MapServer/53"
DEFAULT_CITY_LIMITS_LAYER = "Public/Open_Data_Downloads/MapServer/41"
DEFAULT_FIRE_DISTRICT_LAYER = "Public/Public_Safety_Stations_and_Boundaries/MapServer/11"
DEFAULT_AIRPORT_OVERLAY_LAYER = "Public/AirportOverlay/MapServer/0"
DEFAULT_ZIP_CODE_LAYER = "Public/RowanZipCodes/MapServer/0"
PARCEL_FLOOD_STATUS_LAYER = "Parcel Flood Status"
CITY_LIMITS_LAYER = "City Limits"
SUBDIVISION_LAYER = "Subdivision"
ETJ_LAYER = "ETJ"
FIRE_DISTRICT_LAYER = "Fire District"
AIRPORT_OVERLAY_LAYER = "Airport Overlay"
ZIP_CODE_LAYER = "ZIP Code"

PARCEL_REPORT_LAYERS = {
    "parcels": {"id": 0, "name": "Parcels"},
    "county_zoning": {"id": 1, "name": "County Zoning"},
    "addressing": {"id": 2, "name": "Addressing Points"},
    "flood_zone": {"id": 3, "name": "Flood Zone 2014"},
    "fema_flood_panel": {"id": 4, "name": "FEMA Flood Panel"},
    "watersheds": {"id": 5, "name": "Water Supply Watersheds"},
    "schools": {"id": 6, "name": "School Attendance Areas"},
    "soils": {"id": 7, "name": "Soils"},
    "voting": {"id": 8, "name": "Voting Precincts"},
    "parks": {"id": 9, "name": "County Parks"},
    "all_zoning": {"id": 10, "name": "All Zoning"},
}

# Names returned by ParcelReport identify (used by parcel_report formatter).
PARCEL_REPORT_IDENTIFY_NAMES = {
    "county_zoning": "COUNTY ZONING",
    "all_zoning": "ALL ZONING",
    "flood_zone": "Flood Zone 2014",
    "fema_flood_panel": "FEMA Flood Panel",
    "schools": "School Attendance Areas",
    "soils": "Soils",
    "voting": "Voting Precincts",
    "watersheds": "WATER SUPPLY WATERSHEDS",
    "parks": "County Parks",
}

PARCEL_CONTEXT_LAYER_KEYS = [
    "county_zoning",
    "all_zoning",
    "flood_zone",
    "fema_flood_panel",
    "schools",
    "soils",
    "voting",
    "watersheds",
    "parks",
]


def _layer_url(layer_path: str) -> str:
    return f"{get_base_url()}/{layer_path.lstrip('/')}"


STREET_TYPE_TOKENS = {
    "ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "LN", "LANE", "AVE", "AVENUE",
    "BLVD", "CT", "COURT", "WAY", "CIR", "CIRCLE", "TRL", "TRAIL", "HWY", "HIGHWAY",
    "PKWY", "PARKWAY", "PL", "PLACE",
}


def _street_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", value.upper())
    stopwords = {"THE", "SUBDIVISION", "SUB", "NC", "ROWAN", "COUNTY", "ON", "IN"}
    return [
        token for token in tokens
        if len(token) >= 2 and token not in stopwords and token not in STREET_TYPE_TOKENS
    ]


def _like_tokens(field: str, value: str, *, street_mode: bool = False) -> str:
    tokens = _street_tokens(value) if street_mode else subdivision_search_tokens(value)
    if street_mode and not tokens:
        tokens = re.findall(r"[A-Za-z0-9]+", value.upper())
        stopwords = {"THE", "SUBDIVISION", "SUB", "SUBD", "NC", "ROWAN", "COUNTY"}
        tokens = [token for token in tokens if len(token) >= 2 and token not in stopwords]
    if not tokens:
        token = normalize_subdivision_query(value).upper().replace("'", "''")
        return f"UPPER({field}) LIKE '%{token}%'"
    clauses = [f"UPPER({field}) LIKE '%{token.replace(chr(39), chr(39)*2)}%'" for token in tokens]
    return " AND ".join(clauses)


_subdivision_catalog: list[str] | None = None


def get_subdivision_catalog() -> list[str]:
    global _subdivision_catalog
    if _subdivision_catalog is None:
        payload = list_approved_subdivisions(limit=500)
        _subdivision_catalog = sorted({
            (feature.get("properties") or {}).get("SubName", "").strip()
            for feature in (payload.get("features") or [])
            if (feature.get("properties") or {}).get("SubName")
        })
    return _subdivision_catalog


def _query_subdivision_layer(name: str) -> dict[str, Any]:
    where = _like_tokens("SubName", name)
    approved = query_layer_raw(
        get_approved_subdivision_layer_path(),
        where=where,
        out_fields="SubName,Twsp,Lots,Acres,PlatBook,PlatPage",
        result_record_count=5,
    )
    if approved.get("features"):
        return _normalize_subdivision_geojson(approved)

    where = _like_tokens("SUBNAME", name)
    fallback = query_layer_raw(
        SEARCH_SUBDIVISION_LAYER,
        where=where,
        out_fields="SUBNAME,SUBID,PLATBOOK,PLATPAGE",
        result_record_count=5,
    )
    return _normalize_subdivision_geojson(fallback)


def suggest_subdivision_name(subdivision_name: str) -> str | None:
    normalized = normalize_subdivision_query(subdivision_name)
    if not normalized:
        return None
    return fuzzy_best_match(normalized, get_subdivision_catalog(), min_ratio=0.72)


def canonical_subdivision_name(subdivisions: dict[str, Any], fallback: str) -> str:
    features = subdivisions.get("features") or []
    if not features:
        return normalize_subdivision_query(fallback)
    properties = features[0].get("properties") or {}
    return properties.get("SubName") or properties.get("SUBNAME") or normalize_subdivision_query(fallback)


def query_layer_raw(
    layer_path: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    return_geometry: bool = True,
    result_record_count: int = 100,
    geometry: dict[str, Any] | None = None,
    geometry_type: str | None = None,
    spatial_rel: str | None = None,
    out_sr: int = 4326,
) -> dict[str, Any]:
    url = urljoin(_layer_url(layer_path) + "/", "query")
    params: dict[str, Any] = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "f": "geojson",
        "outSR": out_sr,
        "resultRecordCount": result_record_count,
    }
    if geometry is not None:
        params["geometry"] = json.dumps(geometry)
        params["geometryType"] = geometry_type or "esriGeometryPolygon"
        params["spatialRel"] = spatial_rel or "esriSpatialRelIntersects"
        params["inSR"] = out_sr
        response = requests.post(url, data=params, headers=DEFAULT_HEADERS, timeout=60)
    else:
        response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    return payload


def search_street_centerlines(street_name: str, *, limit: int = 25) -> dict[str, Any]:
    where = _like_tokens("Whole_Name", street_name, street_mode=True)
    return query_layer_raw(
        SEARCH_STREET_LAYER,
        where=where,
        out_fields="Whole_Name,ROAD_NAME,ROAD_TYPE,CITYL,CITYR",
        result_record_count=limit,
    )


def _polyline_from_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    geometry = feature.get("geometry") or {}
    if geometry.get("type") == "LineString":
        return {"paths": [geometry["coordinates"]], "spatialReference": {"wkid": 4326}}
    if geometry.get("type") == "MultiLineString" and geometry.get("coordinates"):
        return {"paths": geometry["coordinates"], "spatialReference": {"wkid": 4326}}
    return None


def count_addresses_on_street(street_name: str, *, limit: int = 500) -> dict[str, Any]:
    """
    Count houses/addresses on a street.
    Resolves the street via Whole_Name (search/MapServer/1), then spatially
    queries addressing points (search/MapServer/0). Falls back to text match.
    """
    streets = search_street_centerlines(street_name, limit=10)
    street_features = streets.get("features") or []

    for street_feature in street_features:
        polyline = _polyline_from_feature(street_feature)
        if not polyline:
            continue
        url = urljoin(_layer_url(SEARCH_ADDRESS_LAYER) + "/", "query")
        params: dict[str, Any] = {
            "where": "1=1",
            "geometry": json.dumps(polyline),
            "geometryType": "esriGeometryPolyline",
            "spatialRel": "esriSpatialRelIntersects",
            "distance": 75,
            "units": "esriSRUnit_Foot",
            "inSR": 4326,
            "outFields": "Address,ROAD_NAME,ROAD_TYPE,COMM,FTRCODE",
            "returnGeometry": "true",
            "f": "geojson",
            "outSR": 4326,
            "resultRecordCount": limit,
        }
        response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("features"):
            return payload

    where = (
        f"({_like_tokens('ROAD_NAME', street_name, street_mode=True)}) OR "
        f"({_like_tokens('Address', street_name, street_mode=True)})"
    )
    return query_layer_raw(
        SEARCH_ADDRESS_LAYER,
        where=where,
        out_fields="Address,ROAD_NAME,ROAD_TYPE,COMM,FTRCODE",
        result_record_count=limit,
    )


def get_approved_subdivision_layer_path() -> str:
    return os.getenv(
        "APPROVED_SUBDIVISION_LAYER_URL",
        DEFAULT_APPROVED_SUBDIVISION_LAYER,
    ).lstrip("/")


def _normalize_subdivision_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    for feature in payload.get("features") or []:
        props = feature.setdefault("properties", {})
        sub_name = props.get("SubName") or props.get("SUBNAME")
        if sub_name:
            props["SUBNAME"] = sub_name
            props["SubName"] = sub_name
    return payload


def list_approved_subdivisions(*, limit: int = 500) -> dict[str, Any]:
    payload = query_layer_raw(
        get_approved_subdivision_layer_path(),
        where="SubName IS NOT NULL",
        out_fields="SubName,Twsp,Lots",
        return_geometry=False,
        result_record_count=limit,
    )
    return _normalize_subdivision_geojson(payload)


def search_subdivision(subdivision_name: str) -> dict[str, Any]:
    normalized = normalize_subdivision_query(subdivision_name)
    candidates = [name for name in (normalized, subdivision_name.strip()) if name]

    for candidate in dict.fromkeys(candidates):
        result = _query_subdivision_layer(candidate)
        if result.get("features"):
            return result

    for token in subdivision_search_tokens(normalized or subdivision_name):
        result = _query_subdivision_layer(token)
        if result.get("features"):
            return result

    suggestion = suggest_subdivision_name(normalized or subdivision_name)
    if suggestion:
        return _query_subdivision_layer(suggestion)

    return {"type": "FeatureCollection", "features": []}


def _polygon_from_geometry(geometry: dict[str, Any]) -> dict[str, Any] | None:
    if geometry.get("type") == "Polygon" and geometry.get("coordinates"):
        return {"rings": geometry["coordinates"], "spatialReference": {"wkid": 4326}}
    if geometry.get("type") == "MultiPolygon" and geometry.get("coordinates"):
        return {"rings": geometry["coordinates"][0], "spatialReference": {"wkid": 4326}}
    return None


def _polygon_from_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    return _polygon_from_geometry(feature.get("geometry") or {})


def addresses_in_subdivision(subdivision_name: str, *, limit: int = 500) -> dict[str, Any]:
    subdivisions = search_subdivision(subdivision_name)
    features = subdivisions.get("features") or []
    if not features:
        return {"type": "FeatureCollection", "features": []}

    polygon = _polygon_from_feature(features[0])
    if not polygon:
        return {"type": "FeatureCollection", "features": []}

    return query_layer_raw(
        SEARCH_ADDRESS_LAYER,
        where="1=1",
        out_fields="Address,ROAD_NAME,COMM,FTRCODE",
        geometry=polygon,
        geometry_type="esriGeometryPolygon",
        result_record_count=limit,
    )


def parcels_in_subdivision(subdivision_name: str, *, limit: int = 500) -> dict[str, Any]:
    subdivisions = search_subdivision(subdivision_name)
    features = subdivisions.get("features") or []
    if not features:
        return {"type": "FeatureCollection", "features": []}

    polygon = _polygon_from_feature(features[0])
    if not polygon:
        return {"type": "FeatureCollection", "features": []}

    return query_layer_raw(
        get_parcel_layer_path(),
        where="1=1",
        out_fields="PIN,PARCEL_ID,OWNNAME,PROP_ADDRESS,CITY,TOT_VAL",
        geometry=polygon,
        geometry_type="esriGeometryPolygon",
        result_record_count=limit,
    )


def parcels_and_addresses_in_subdivision(subdivision_name: str, *, limit: int = 500) -> dict[str, Any]:
    parcels = parcels_in_subdivision(subdivision_name, limit=limit)
    addresses = addresses_in_subdivision(subdivision_name, limit=limit)
    features = (parcels.get("features") or []) + (addresses.get("features") or [])
    return {"type": "FeatureCollection", "features": features}


def identify_parcel_report_at_point(x: float, y: float, *, layers: str = "all") -> dict[str, Any]:
    """Identify ParcelReport layers at a point for report/context enrichment."""
    url = urljoin(_layer_url(PARCEL_REPORT_SERVICE) + "/", "identify")
    params = {
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "sr": 4326,
        "layers": layers,
        "tolerance": 5,
        "mapExtent": "-81,35,-80,36",
        "imageDisplay": "800,600,96",
        "f": "json",
    }

    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def query_parcel_report_by_polygon(
    geometry: dict[str, Any],
    *,
    layer_keys: list[str] | None = None,
) -> dict[str, Any]:
    """
    Query ParcelReport sublayers that intersect a parcel polygon.
    More accurate than point identify for zoning, flood, schools, etc.
    """
    polygon = _polygon_from_geometry(geometry)
    if not polygon:
        return {"results": []}

    keys = layer_keys or PARCEL_CONTEXT_LAYER_KEYS
    results: list[dict[str, Any]] = []

    def fetch_layer(key: str) -> list[dict[str, Any]]:
        meta = PARCEL_REPORT_LAYERS.get(key)
        if not meta:
            return []
        layer_path = f"{PARCEL_REPORT_SERVICE}/{meta['id']}"
        layer_name = PARCEL_REPORT_IDENTIFY_NAMES.get(key, meta["name"])
        try:
            payload = query_layer_raw(
                layer_path,
                where="1=1",
                out_fields="*",
                geometry=polygon,
                geometry_type="esriGeometryPolygon",
                result_record_count=25,
            )
        except (requests.RequestException, RuntimeError):
            return []

        layer_results: list[dict[str, Any]] = []
        for feature in payload.get("features") or []:
            properties = feature.get("properties") or {}
            layer_results.append(
                {
                    "layerId": meta["id"],
                    "layerName": layer_name,
                    "attributes": properties,
                }
            )
        return layer_results

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_layer, key): key for key in keys}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                continue

    return {"results": results}


def get_parcel_report_catalog() -> list[dict[str, Any]]:
    return [
        {"layer_id": meta["id"], "name": meta["name"], "service": PARCEL_REPORT_SERVICE}
        for meta in PARCEL_REPORT_LAYERS.values()
    ]


def get_city_limits_layer_path() -> str:
    return os.getenv("CITY_LIMITS_LAYER_URL", DEFAULT_CITY_LIMITS_LAYER).lstrip("/")


def query_city_limits_at_point(x: float, y: float) -> str | None:
    """Return municipality name when a point falls inside city limits."""
    url = urljoin(_layer_url(get_city_limits_layer_path()) + "/", "query")
    params = {
        "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "CITY_NAME",
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": 1,
    }

    try:
        response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return None

    if payload.get("error"):
        return None

    features = payload.get("features") or []
    if not features:
        return None

    city_name = (features[0].get("attributes") or {}).get("CITY_NAME")
    if city_name is None:
        return None
    text = str(city_name).strip()
    return text or None


def enrich_parcel_report_city_limits(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    """Add city-limits context using the Open Data city limits layer."""
    from services.parcel_address_lookup import geocode_from_parcel_feature

    parcel_features = _parcel_polygon_features(geojson)
    if len(parcel_features) != 1:
        return parcel_report

    parcel_geocode = geocode_from_parcel_feature(parcel_features[0])
    if not parcel_geocode or not parcel_geocode.get("location"):
        return parcel_report

    location = parcel_geocode["location"]
    city_name = query_city_limits_at_point(location["x"], location["y"])

    report = dict(parcel_report or {"results": []})
    results = [
        item for item in (report.get("results") or [])
        if item.get("layerName") != CITY_LIMITS_LAYER
    ]
    results.insert(
        0,
        {
            "layerName": CITY_LIMITS_LAYER,
            "attributes": {"CITY_NAME": city_name},
        },
    )
    report["results"] = results
    return report


def get_flood_parcel_layer_path() -> str:
    return os.getenv("FLOOD_PARCEL_LAYER_URL", DEFAULT_FLOOD_PARCEL_LAYER).lstrip("/")


def query_parcel_flood_status(parcel_id: str) -> dict[str, Any] | None:
    """
    Look up whether a tax parcel intersects mapped flood area.

    Uses Public/Open_Data_Downloads MapServer layer 53.

    Rowan field semantics (from GIS data review):
    - FID_Flood_Dissolve = -1  → parcel is NOT in a flood zone polygon
    - FID_Flood_Dissolve >= 0  → parcel overlaps flood; AC_in_Flood is overlap acres
    - When FID = -1, AC_in_Flood matches total parcel acreage (not flood acreage)
    """
    cleaned = str(parcel_id or "").strip()
    if not cleaned:
        return None

    escaped = cleaned.replace("'", "''")
    url = urljoin(_layer_url(get_flood_parcel_layer_path()) + "/", "query")
    params = {
        "where": f"PARCEL_ID = '{escaped}'",
        "outFields": "PARCEL_ID,AC_in_Flood,FID_Flood_Dissolve",
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": 1,
    }

    try:
        response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return None

    if payload.get("error"):
        return None

    features = payload.get("features") or []
    if not features:
        return None

    attrs = features[0].get("attributes") or {}
    fid_raw = attrs.get("FID_Flood_Dissolve")
    try:
        fid = int(fid_raw) if fid_raw is not None else -1
    except (TypeError, ValueError):
        fid = -1

    try:
        acres = float(attrs.get("AC_in_Flood") or 0)
    except (TypeError, ValueError):
        acres = 0.0

    has_flood = fid >= 0 and acres > 0.000001

    return {
        "PARCEL_ID": attrs.get("PARCEL_ID") or cleaned,
        "AC_in_Flood": acres if has_flood else 0.0,
        "parcel_acres_field": acres if fid < 0 else None,
        "has_flood": has_flood,
        "FID_Flood_Dissolve": fid,
    }


def _parcel_polygon_features(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for feature in geojson.get("features") or []:
        props = feature.get("properties") or {}
        if props.get("_lookup") == "address_point":
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        if props.get("PIN") or props.get("PARCEL_ID"):
            matches.append(feature)
    return matches


def enrich_parcel_report_flood(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    """Add parcel-level flood yes/no using Open Data flood acreage layer."""
    parcel_features = _parcel_polygon_features(geojson)
    if len(parcel_features) != 1:
        return parcel_report

    parcel_id = (parcel_features[0].get("properties") or {}).get("PARCEL_ID")
    flood = query_parcel_flood_status(parcel_id or "")
    if not flood:
        return parcel_report

    report = dict(parcel_report or {"results": []})
    results = [
        item for item in (report.get("results") or [])
        if item.get("layerName") != PARCEL_FLOOD_STATUS_LAYER
    ]
    results.insert(
        0,
        {
            "layerName": PARCEL_FLOOD_STATUS_LAYER,
            "attributes": flood,
        },
    )
    report["results"] = results
    return report


def get_fire_district_layer_path() -> str:
    return os.getenv("FIRE_DISTRICT_LAYER_URL", DEFAULT_FIRE_DISTRICT_LAYER).lstrip("/")


def get_airport_overlay_layer_path() -> str:
    return os.getenv("AIRPORT_OVERLAY_LAYER_URL", DEFAULT_AIRPORT_OVERLAY_LAYER).lstrip("/")


def get_zip_code_layer_path() -> str:
    return os.getenv("ZIP_CODE_LAYER_URL", DEFAULT_ZIP_CODE_LAYER).lstrip("/")


def _query_polygon_layer(
    layer_path: str,
    polygon: dict[str, Any],
    *,
    out_fields: str,
    result_record_count: int = 5,
) -> list[dict[str, Any]]:
    url = urljoin(_layer_url(layer_path) + "/", "query")
    params = {
        "geometry": json.dumps(polygon),
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": 4326,
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": result_record_count,
    }
    try:
        response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return []

    if payload.get("error"):
        return []
    return payload.get("features") or []


def _parcel_polygon_from_geojson(geojson: dict[str, Any]) -> dict[str, Any] | None:
    parcel_features = _parcel_polygon_features(geojson)
    if len(parcel_features) != 1:
        return None
    return _polygon_from_geometry(parcel_features[0].get("geometry") or {})


def query_subdivision_for_polygon(polygon: dict[str, Any]) -> dict[str, Any] | None:
    layer_queries = (
        (get_approved_subdivision_layer_path(), "SubName,Twsp,Lots,PlatBook,PlatPage"),
        (SEARCH_SUBDIVISION_LAYER, "SUBNAME,SUBID,PLATBOOK,PLATPAGE"),
    )
    for layer_path, out_fields in layer_queries:
        features = _query_polygon_layer(
            layer_path,
            polygon,
            out_fields=out_fields,
            result_record_count=3,
        )
        for feature in features:
            attrs = feature.get("attributes") or {}
            name = str(attrs.get("SubName") or attrs.get("SUBNAME") or "").strip()
            if not name:
                continue
            result = {"SUBNAME": name, "SubName": name}
            township = attrs.get("Twsp") or attrs.get("TOWNSHIP")
            if township:
                result["TOWNSHIP"] = township
            if attrs.get("SUBID") is not None:
                result["SUBID"] = attrs.get("SUBID")
            if attrs.get("Lots") is not None:
                result["LOTS"] = attrs.get("Lots")
            return result
    return None


def query_fire_district_for_polygon(polygon: dict[str, Any]) -> dict[str, Any] | None:
    features = _query_polygon_layer(
        get_fire_district_layer_path(),
        polygon,
        out_fields="MAIN_DISTRICT,CAD,DISTRICT_NUM",
        result_record_count=3,
    )
    if not features:
        return None

    attrs = features[0].get("attributes") or {}
    district = str(attrs.get("MAIN_DISTRICT") or "").strip()
    if not district:
        return None
    return {
        "MAIN_DISTRICT": district,
        "CAD": attrs.get("CAD"),
        "DISTRICT_NUM": attrs.get("DISTRICT_NUM"),
    }


def query_airport_overlay_for_polygon(polygon: dict[str, Any]) -> bool:
    features = _query_polygon_layer(
        get_airport_overlay_layer_path(),
        polygon,
        out_fields="OBJECTID",
        result_record_count=1,
    )
    return bool(features)


def query_zip_code_for_polygon(polygon: dict[str, Any]) -> str | None:
    features = _query_polygon_layer(
        get_zip_code_layer_path(),
        polygon,
        out_fields="ZIP_CODE,COMMUNITY",
        result_record_count=1,
    )
    if not features:
        return None
    zip_code = (features[0].get("attributes") or {}).get("ZIP_CODE")
    if zip_code is None:
        return None
    text = str(zip_code).strip()
    return text or None


def query_county_zoning_for_polygon(polygon: dict[str, Any]) -> dict[str, Any] | None:
    layer_path = f"{PARCEL_REPORT_SERVICE}/1"
    features = _query_polygon_layer(layer_path, polygon, out_fields="ZONING", result_record_count=5)
    if not features:
        return None

    attrs = features[0].get("attributes") or {}
    zoning = str(attrs.get("ZONING") or "").strip()
    if not zoning:
        return None
    return {"ZONING": zoning}


def _upsert_report_layer(
    parcel_report: dict[str, Any] | None,
    *,
    layer_name: str,
    attributes: dict[str, Any],
    insert_at: int = 0,
) -> dict[str, Any]:
    report = dict(parcel_report or {"results": []})
    results = [item for item in (report.get("results") or []) if item.get("layerName") != layer_name]
    results.insert(insert_at, {"layerName": layer_name, "attributes": attributes})
    report["results"] = results
    return report


def enrich_parcel_report_subdivision(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    polygon = _parcel_polygon_from_geojson(geojson)
    if not polygon:
        return parcel_report

    subdivision = query_subdivision_for_polygon(polygon)
    if not subdivision:
        return parcel_report

    return _upsert_report_layer(
        parcel_report,
        layer_name=SUBDIVISION_LAYER,
        attributes=subdivision,
        insert_at=1,
    )


def enrich_parcel_report_etj(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    from services.parcel_facts import municipality_from_county_zoning

    polygon = _parcel_polygon_from_geojson(geojson)
    if not polygon:
        return parcel_report

    county_zoning = query_county_zoning_for_polygon(polygon)
    municipality = municipality_from_county_zoning(
        (county_zoning or {}).get("ZONING") if county_zoning else None
    )
    if not municipality:
        return parcel_report

    in_city_limits = False
    for item in (parcel_report or {}).get("results") or []:
        if item.get("layerName") != CITY_LIMITS_LAYER:
            continue
        city_name = (item.get("attributes") or {}).get("CITY_NAME")
        if city_name:
            in_city_limits = True
            break

    if in_city_limits:
        return parcel_report

    return _upsert_report_layer(
        parcel_report,
        layer_name=ETJ_LAYER,
        attributes={"MUNICIPALITY": municipality},
        insert_at=2,
    )


def enrich_parcel_report_fire_district(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    polygon = _parcel_polygon_from_geojson(geojson)
    if not polygon:
        return parcel_report

    fire = query_fire_district_for_polygon(polygon)
    if not fire:
        return parcel_report

    return _upsert_report_layer(
        parcel_report,
        layer_name=FIRE_DISTRICT_LAYER,
        attributes=fire,
    )


def enrich_parcel_report_airport_overlay(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    polygon = _parcel_polygon_from_geojson(geojson)
    if not polygon:
        return parcel_report

    if not query_airport_overlay_for_polygon(polygon):
        return parcel_report

    return _upsert_report_layer(
        parcel_report,
        layer_name=AIRPORT_OVERLAY_LAYER,
        attributes={"in_overlay": True},
    )


def enrich_parcel_report_zip_code(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    parcel_features = _parcel_polygon_features(geojson)
    if len(parcel_features) == 1:
        props = parcel_features[0].get("properties") or {}
        if str(props.get("ZIPCODE") or "").strip():
            return parcel_report

    polygon = _parcel_polygon_from_geojson(geojson)
    if not polygon:
        return parcel_report

    zip_code = query_zip_code_for_polygon(polygon)
    if not zip_code:
        return parcel_report

    return _upsert_report_layer(
        parcel_report,
        layer_name=ZIP_CODE_LAYER,
        attributes={"ZIP_CODE": zip_code},
    )


def enrich_parcel_report_context(
    parcel_report: dict[str, Any] | None,
    geojson: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply supplemental parcel context enrichers (GIS calls run in parallel)."""
    from services.parcel_address_lookup import _feature_centroid
    from services.parcel_facts import municipality_from_county_zoning

    parcel_features = _parcel_polygon_features(geojson)
    if len(parcel_features) != 1:
        return parcel_report

    feature = parcel_features[0]
    props = feature.get("properties") or {}
    parcel_id = str(props.get("PARCEL_ID") or "").strip()
    polygon = _polygon_from_geometry(feature.get("geometry") or {})
    centroid = _feature_centroid(feature)

    futures: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        if parcel_id:
            futures["flood"] = executor.submit(query_parcel_flood_status, parcel_id)
        if centroid:
            futures["city"] = executor.submit(query_city_limits_at_point, centroid[0], centroid[1])
        if polygon:
            futures["subdivision"] = executor.submit(query_subdivision_for_polygon, polygon)
            futures["fire"] = executor.submit(query_fire_district_for_polygon, polygon)
            futures["airport"] = executor.submit(query_airport_overlay_for_polygon, polygon)
            futures["county_zoning"] = executor.submit(query_county_zoning_for_polygon, polygon)
            if not str(props.get("ZIPCODE") or "").strip():
                futures["zip"] = executor.submit(query_zip_code_for_polygon, polygon)

        results = {name: future.result() for name, future in futures.items()}

    report = dict(parcel_report or {"results": []})

    flood = results.get("flood")
    if flood:
        report = _upsert_report_layer(
            report,
            layer_name=PARCEL_FLOOD_STATUS_LAYER,
            attributes=flood,
            insert_at=0,
        )

    report = _upsert_report_layer(
        report,
        layer_name=CITY_LIMITS_LAYER,
        attributes={"CITY_NAME": results.get("city")},
        insert_at=0,
    )

    subdivision = results.get("subdivision")
    if subdivision:
        report = _upsert_report_layer(
            report,
            layer_name=SUBDIVISION_LAYER,
            attributes=subdivision,
            insert_at=1,
        )

    county_zoning = results.get("county_zoning")
    municipality = municipality_from_county_zoning(
        (county_zoning or {}).get("ZONING") if county_zoning else None
    )
    in_city_limits = bool(results.get("city"))
    if municipality and not in_city_limits:
        report = _upsert_report_layer(
            report,
            layer_name=ETJ_LAYER,
            attributes={"MUNICIPALITY": municipality},
            insert_at=2,
        )

    fire = results.get("fire")
    if fire:
        report = _upsert_report_layer(
            report,
            layer_name=FIRE_DISTRICT_LAYER,
            attributes=fire,
        )

    if results.get("airport"):
        report = _upsert_report_layer(
            report,
            layer_name=AIRPORT_OVERLAY_LAYER,
            attributes={"in_overlay": True},
        )

    zip_code = results.get("zip")
    if zip_code:
        report = _upsert_report_layer(
            report,
            layer_name=ZIP_CODE_LAYER,
            attributes={"ZIP_CODE": zip_code},
        )

    return report

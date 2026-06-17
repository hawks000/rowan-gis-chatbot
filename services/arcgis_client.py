"""Thin client for Rowan County public ArcGIS REST MapServer query endpoints."""

import os
from typing import Any
from urllib.parse import urljoin

import requests

DEFAULT_BASE = "https://gis.rowancountync.gov/arcgis/rest/services"
DEFAULT_PARCEL_LAYER = "Public/RowanTaxParcels/MapServer/0"

DISPLAY_FIELDS = [
    "PIN",
    "PARCEL_ID",
    "OWNNAME",
    "OWN2",
    "PROP_ADDRESS",
    "TAXADD1",
    "CITY",
    "ZIPCODE",
    "TOT_VAL",
    "LANDFMV",
    "IMP_FMV",
    "CALCACRE",
    "TOWNSHIP",
    "TAX_DISTRICT",
    "DATESOLD",
    "SALE_AMT",
    "DEEDBOOK",
    "DEEDPAGE",
    "PLATBOOK",
    "PLATPAGE",
]

ADDRESS_DISPLAY_FIELDS = ["Address", "ROAD_NAME", "ROAD_TYPE", "COMM", "FTRCODE"]
STREET_DISPLAY_FIELDS = ["Whole_Name", "ROAD_NAME", "ROAD_TYPE", "CITYL", "CITYR"]
SUBDIVISION_DISPLAY_FIELDS = ["SUBNAME", "SUBID", "PLATBOOK", "PLATPAGE"]

LAYER_CATALOG = [
    {
        "id": "parcels",
        "name": "Rowan Tax Parcels",
        "layer_url": "Public/RowanTaxParcels/MapServer/0",
        "fields": DISPLAY_FIELDS,
        "examples": [
            "Who owns 550 MT HALL RD",
            "Find Earl Hawks owning property",
            "PIN 5733-04-51-7482",
            "How many parcels on Woodleaf",
        ],
    },
    {
        "id": "addressing",
        "name": "Addressing Points",
        "layer_url": "Public/search/MapServer/0",
        "fields": ADDRESS_DISPLAY_FIELDS,
        "examples": [
            "Who owns 550 MT HALL RD",
            "How many houses on Main Street",
        ],
    },
    {
        "id": "streets",
        "name": "Street Centerlines",
        "layer_url": "Public/search/MapServer/1",
        "fields": STREET_DISPLAY_FIELDS,
        "examples": [
            "How many houses on Woodleaf Road",
        ],
    },
    {
        "id": "subdivisions",
        "name": "Subdivisions",
        "layer_url": "Public/IntranetMap/MapServer/27",
        "fields": ["SubName", "Twsp", "Lots", "PlatBook", "PlatPage"],
        "examples": [
            "How many subdivisions are in Rowan County",
            "Parcels in Grand Oaks",
            "List all subdivision names",
            "Addresses in subdivision Oak Hills",
        ],
    },
    {
        "id": "parcel_report",
        "name": "Parcel Report (zoning, flood, schools)",
        "layer_url": "Public/ParcelReport/MapServer",
        "fields": ["zoning", "flood", "schools", "soils", "voting", "parks"],
        "examples": [
            "What is the zoning for 550 MT HALL RD",
            "What flood zone is PIN 5733-04-51-7482 in",
            "Schools for 550 MT HALL RD",
            "Property info for 550 MT HALL RD",
        ],
    },
]


DEFAULT_HEADERS = {"User-Agent": "RowanGISChatbot/1.0 (Rowan County GIS)"}


class ArcGISQueryError(Exception):
    """Raised when ArcGIS REST query fails."""


def get_base_url() -> str:
    return os.getenv("ARCGIS_BASE_URL", DEFAULT_BASE).rstrip("/")


def get_parcel_layer_path() -> str:
    return os.getenv("PARCEL_LAYER_URL", DEFAULT_PARCEL_LAYER).lstrip("/")


def _layer_url(layer_path: str | None = None) -> str:
    path = (layer_path or get_parcel_layer_path()).lstrip("/")
    return f"{get_base_url()}/{path}"


def query_layer(
    where: str,
    *,
    layer_path: str | None = None,
    out_fields: list[str] | None = None,
    result_record_count: int = 25,
    return_geometry: bool = True,
    out_sr: int = 4326,
) -> dict[str, Any]:
    """Execute an attribute query against a MapServer layer and return GeoJSON."""
    url = urljoin(_layer_url(layer_path) + "/", "query")
    params = {
        "where": where,
        "outFields": ",".join(out_fields or DISPLAY_FIELDS),
        "returnGeometry": "true" if return_geometry else "false",
        "f": "geojson",
        "outSR": out_sr,
        "resultRecordCount": result_record_count,
    }

    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise ArcGISQueryError(str(payload["error"]))

    return payload


def query_layer_at_point(
    x: float,
    y: float,
    *,
    layer_path: str | None = None,
    distance_feet: float = 75,
    out_fields: list[str] | None = None,
    result_record_count: int = 10,
    in_sr: int = 4326,
    out_sr: int = 4326,
) -> dict[str, Any]:
    """Spatial parcel query at a geocoded point (NC OneMap → parcel intersect)."""
    url = urljoin(_layer_url(layer_path) + "/", "query")
    params = {
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "inSR": in_sr,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": distance_feet,
        "units": "esriSRUnit_Foot",
        "outFields": ",".join(out_fields or DISPLAY_FIELDS),
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": out_sr,
        "resultRecordCount": result_record_count,
    }

    response = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise ArcGISQueryError(str(payload["error"]))

    return payload


def summarize_features(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract display attributes from GeoJSON features."""
    summaries = []
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        field_list = DISPLAY_FIELDS
        if "Address" in props and "PIN" not in props:
            field_list = ADDRESS_DISPLAY_FIELDS
        elif "Whole_Name" in props and "PIN" not in props:
            field_list = STREET_DISPLAY_FIELDS
        elif "SUBNAME" in props and "PIN" not in props:
            field_list = SUBDIVISION_DISPLAY_FIELDS
        summaries.append({field: props.get(field) for field in field_list if field in props})
    return summaries


def get_layer_catalog() -> list[dict[str, Any]]:
    return LAYER_CATALOG

"""Parse Public/ParcelReport MapServer identify results into chat-friendly summaries."""

from typing import Any

LAYER_ALIASES = {
    "parcels": "Parcels",
    "county_zoning": "COUNTY ZONING",
    "addressing": "Addressing Points",
    "flood_zone": "Flood Zone 2014",
    "fema_flood_panel": "FEMA Flood Panel",
    "watersheds": "WATER SUPPLY WATERSHEDS",
    "schools": "School Attendance Areas",
    "soils": "Soils",
    "voting": "Voting Precincts",
    "parks": "County Parks",
    "all_zoning": "ALL ZONING",
}

FOCUS_LAYER_ORDER = {
    "zoning": ["ALL ZONING"],
    "flood": ["Parcel Flood Status", "Flood Zone 2014", "FEMA Flood Panel"],
    "schools": ["School Attendance Areas"],
    "soils": ["Soils"],
    "voting": ["Voting Precincts"],
    "watershed": ["WATER SUPPLY WATERSHEDS"],
    "parks": ["County Parks"],
    "property_info": [
        "City Limits",
        "Subdivision",
        "ETJ",
        "ALL ZONING",
        "Fire District",
        "Airport Overlay",
        "Parcel Flood Status",
        "Flood Zone 2014",
        "FEMA Flood Panel",
        "School Attendance Areas",
        "Soils",
        "Voting Precincts",
        "WATER SUPPLY WATERSHEDS",
        "County Parks",
    ],
}

FOCUS_EMPTY_MESSAGES = {
    "zoning": "No zoning polygon was found at this location.",
    "flood": "No mapped flood area was found for this parcel.",
    "schools": "No school attendance area was found at this location.",
    "soils": "No soil map unit was found at this location.",
    "voting": "No voting precinct was found at this location.",
    "watershed": "No water supply watershed was found at this location.",
    "parks": "No county park boundary was found at this location.",
}


def _area_value(attributes: dict[str, Any]) -> float:
    for key in ("Shape.STArea()", "SHAPE.STArea()"):
        value = attributes.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return float("inf")


def _unique_field_values(matches: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for item in matches:
        raw = _clean((item.get("attributes") or {}).get(field))
        if raw and raw not in values:
            values.append(raw)
    return values


def _pick_best_for_layer(
    results: list[dict[str, Any]],
    layer_name: str,
    *,
    parcel_pin: str | None = None,
) -> dict[str, Any] | None:
    matches = [item for item in results if item.get("layerName") == layer_name]
    if not matches:
        return None

    if parcel_pin and layer_name == "Parcels":
        for item in matches:
            attrs = item.get("attributes") or {}
            if attrs.get("PIN") == parcel_pin:
                return item

    if layer_name in {"COUNTY ZONING", "ALL ZONING"}:
        field = "ZONING"
        zones = _unique_field_values(matches, field)
        if zones:
            merged_attrs = dict(matches[0].get("attributes") or {})
            merged_attrs[field] = ", ".join(zones)
            return {"layerName": layer_name, "attributes": merged_attrs}

    return min(matches, key=lambda item: _area_value(item.get("attributes") or {}))


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "0"}:
        return None
    return text


def _fema_panel_from_results(results: list[dict[str, Any]]) -> str | None:
    for item in results:
        if item.get("layerName") != "FEMA Flood Panel":
            continue
        attrs = item.get("attributes") or {}
        panel = _clean(attrs.get("PANEL"))
        firm = _clean(attrs.get("FIRM_ID"))
        if panel and firm:
            return f"{panel} (FIRM {firm})"
        return panel or firm
    return None


def _line_for_layer(layer_name: str, attributes: dict[str, Any]) -> str | None:
    if layer_name == "Parcel Flood Status":
        if attributes.get("has_flood"):
            acres = float(attributes.get("AC_in_Flood") or 0)
            return f"Flood on parcel: Yes — {acres:.4f} acres in flood area"
        return "Flood on parcel: No"

    if layer_name == "City Limits":
        city_name = _clean(attributes.get("CITY_NAME"))
        if city_name:
            return f"Location: within {city_name.title()} city limits"
        return "Location: unincorporated Rowan County"

    if layer_name == "Subdivision":
        name = _clean(attributes.get("SUBNAME") or attributes.get("SubName"))
        township = _clean(attributes.get("TOWNSHIP") or attributes.get("Twsp"))
        if name and township:
            return f"Subdivision: {name.title()} ({township.title()} township)"
        return f"Subdivision: {name.title()}" if name else None

    if layer_name == "ETJ":
        municipality = _clean(attributes.get("MUNICIPALITY"))
        return f"ETJ: {municipality.title()} extraterritorial jurisdiction" if municipality else None

    if layer_name == "Fire District":
        district = _clean(attributes.get("MAIN_DISTRICT"))
        cad = _clean(attributes.get("CAD") or attributes.get("DISTRICT_NUM"))
        if district and cad and cad not in district:
            return f"Fire district: {district.title()} (CAD {cad})"
        return f"Fire district: {district.title()}" if district else None

    if layer_name == "Airport Overlay":
        if attributes.get("in_overlay"):
            return "Airport overlay: Yes"
        return None

    if layer_name == "ALL ZONING":
        zoning = _clean(attributes.get("ZONING"))
        district = _clean(attributes.get("District"))
        if zoning and district and district != zoning:
            return f"Zoning: {zoning} ({district})"
        return f"Zoning: {zoning}" if zoning else None

    if layer_name == "COUNTY ZONING":
        return None

    if layer_name == "Flood Zone 2014":
        zone = _clean(attributes.get("FLD_ZONE") or attributes.get("ZONE") or attributes.get("FLOODZONE"))
        return f"Flood zone: {zone}" if zone else None

    if layer_name == "FEMA Flood Panel":
        panel = _clean(attributes.get("PANEL"))
        firm = _clean(attributes.get("FIRM_ID"))
        if panel and firm:
            return f"FEMA map panel: {panel} (FIRM {firm})"
        return f"FEMA map panel: {panel or firm}" if (panel or firm) else None

    if layer_name == "School Attendance Areas":
        parts = [
            _clean(attributes.get("ELEM_School_Name") or attributes.get("Elementary School")),
            _clean(attributes.get("MIDDLE_School_Name") or attributes.get("Middle School")),
            _clean(attributes.get("HIGH_School_Name") or attributes.get("High School")),
        ]
        labels = ["Elementary", "Middle", "High"]
        lines = [f"{label}: {value}" for label, value in zip(labels, parts) if value]
        return "Schools — " + "; ".join(lines) if lines else None

    if layer_name == "Soils":
        soil = _clean(attributes.get("SOIL_ID"))
        group = _clean(attributes.get("GROUP_"))
        septic = _clean(attributes.get("SEPTIC"))
        chunks = [chunk for chunk in (f"Soil {soil}" if soil else None, group, f"Septic: {septic}" if septic else None) if chunk]
        return "; ".join(chunks) if chunks else None

    if layer_name == "Voting Precincts":
        name = _clean(attributes.get("PRECINCT_NAME") or attributes.get("NAME"))
        number = _clean(attributes.get("PRECINCT_NUMBER") or attributes.get("PRECINCT"))
        if name and number and number not in name:
            return f"Voting precinct: {name} (#{number})"
        precinct = name or number or _clean(attributes.get("POLLING"))
        return f"Voting precinct: {precinct}" if precinct else None

    if layer_name == "WATER SUPPLY WATERSHEDS":
        stream = _clean(attributes.get("Stream_Name"))
        basin = _clean(attributes.get("River_Basin"))
        classification = _clean(attributes.get("Class"))
        chunks = [chunk for chunk in (classification, basin, stream) if chunk]
        return "Watershed — " + "; ".join(chunks) if chunks else None

    if layer_name == "County Parks":
        name = _clean(attributes.get("NAME") or attributes.get("PARK_NAME"))
        return f"Nearby park: {name}" if name else None

    return None


def summarize_parcel_report(
    identify_response: dict[str, Any] | None,
    *,
    focus: str = "",
    parcel_pin: str | None = None,
) -> dict[str, Any]:
    """Build structured summary and display lines from an identify response."""
    if not identify_response or not identify_response.get("results"):
        lines: list[str] = []
        if focus and focus != "property_info":
            empty_message = FOCUS_EMPTY_MESSAGES.get(focus)
            if empty_message:
                lines.append(empty_message)
        return {"lines": lines, "by_layer": {}}

    results = identify_response["results"]
    layer_names = list(dict.fromkeys(item.get("layerName") for item in results if item.get("layerName")))
    by_layer: dict[str, dict[str, Any]] = {}

    for layer_name in layer_names:
        best = _pick_best_for_layer(results, layer_name, parcel_pin=parcel_pin)
        if best:
            by_layer[layer_name] = best.get("attributes") or {}

    order = FOCUS_LAYER_ORDER.get(focus, layer_names)
    lines: list[str] = []
    seen: set[str] = set()
    fema_panel = _fema_panel_from_results(results)

    if focus == "flood":
        for layer_name in order:
            attrs = by_layer.get(layer_name)
            if not attrs:
                continue
            line = _line_for_layer(layer_name, attrs)
            if not line:
                continue
            if layer_name == "Parcel Flood Status" and fema_panel and attrs.get("has_flood"):
                line = f"{line} (FEMA panel {fema_panel})"
            lines.append(line)
            break
    elif focus == "zoning":
        for layer_name in order:
            attrs = by_layer.get(layer_name)
            if not attrs:
                continue
            line = _line_for_layer(layer_name, attrs)
            if line and line not in seen:
                lines.append(line)
                seen.add(line)
                break
    elif focus == "property_info" or not focus:
        skip_fema_panel = False
        for layer_name in order:
            attrs = by_layer.get(layer_name)
            if layer_name in {"City Limits", "Subdivision", "ETJ"}:
                line = _line_for_layer(layer_name, attrs or {})
            elif not attrs:
                continue
            else:
                if layer_name == "FEMA Flood Panel" and skip_fema_panel:
                    continue
                line = _line_for_layer(layer_name, attrs)
            if not line or line in seen:
                continue
            if layer_name == "Parcel Flood Status" and fema_panel and attrs and attrs.get("has_flood"):
                line = f"{line} (FEMA panel {fema_panel})"
                skip_fema_panel = True
            lines.append(line)
            seen.add(line)
    elif focus:
        for layer_name in order:
            attrs = by_layer.get(layer_name)
            if not attrs:
                continue
            line = _line_for_layer(layer_name, attrs)
            if line and line not in seen:
                lines.append(line)
                break

    if not lines and focus and focus != "property_info":
        empty_message = FOCUS_EMPTY_MESSAGES.get(focus)
        if empty_message:
            lines.append(empty_message)

    return {"lines": lines, "by_layer": by_layer, "layer_names": layer_names}


def format_property_details(
    identify_response: dict[str, Any] | None,
    *,
    focus: str = "",
    parcel_pin: str | None = None,
) -> list[str]:
    """Return human-readable property context lines (zoning, schools, flood, etc.)."""
    if not identify_response:
        return []
    effective_focus = focus or "property_info"
    summary = summarize_parcel_report(
        identify_response,
        focus=effective_focus,
        parcel_pin=parcel_pin,
    )
    return summary.get("lines") or []


def format_context_note(
    identify_response: dict[str, Any] | None,
    *,
    focus: str = "",
    parcel_pin: str | None = None,
) -> str:
    """Return a sentence fragment to append to the chat response."""
    lines = format_property_details(identify_response, focus=focus, parcel_pin=parcel_pin)
    if not lines:
        return ""

    if focus and len(lines) == 1:
        return f" {lines[0]}."

    if focus:
        return " " + " ".join(f"{line}." for line in lines)

    return ""

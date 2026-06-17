"""Format tax-parcel attribute rows into readable property panel lines."""

from datetime import datetime, timezone
from typing import Any

MUNICIPAL_ETJ_MARKERS = (
    " city",
    " town",
    " village",
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "0"}:
        return None
    return text


def _format_currency(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return f"${value:,.0f}"


def _format_sale_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and value > 1_000_000_000_000:
            dt = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        elif isinstance(value, (int, float)) and value > 10_000:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            text = _clean(value)
            if not text:
                return None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                return text
        return dt.strftime("%B %d, %Y")
    except (TypeError, ValueError, OSError):
        return _clean(value)


def _format_zip(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    if "-" in text:
        base, suffix = text.split("-", 1)
        if base.isdigit() and suffix.isdigit():
            return f"{base}-{suffix[:5].lstrip('0') or suffix[:5]}"
    return text


def format_parcel_attribute_lines(row: dict[str, Any]) -> list[str]:
    """Return core tax-parcel facts for the property panel."""
    lines: list[str] = []

    acres = row.get("CALCACRE")
    if isinstance(acres, (int, float)) and acres > 0:
        lines.append(f"Acreage: {acres:.2f} acres")

    township = _clean(row.get("TOWNSHIP"))
    if township:
        lines.append(f"Township: {township.title()}")

    tax_district = _clean(row.get("TAX_DISTRICT"))
    if tax_district:
        lines.append(f"Tax district: {tax_district.title()}")

    zip_code = _format_zip(row.get("ZIPCODE"))
    if zip_code:
        lines.append(f"ZIP: {zip_code}")

    land_value = _format_currency(row.get("LANDFMV"))
    if land_value:
        lines.append(f"Land value: {land_value}")

    improvement_value = _format_currency(row.get("IMP_FMV"))
    if improvement_value:
        lines.append(f"Improvement value: {improvement_value}")

    total_value = _format_currency(row.get("TOT_VAL"))
    if total_value:
        lines.append(f"Total value: {total_value}")

    sale_date = _format_sale_date(row.get("DATESOLD"))
    if sale_date:
        sale_amount = _format_currency(row.get("SALE_AMT"))
        if sale_amount:
            lines.append(f"Date sold: {sale_date} ({sale_amount})")
        else:
            lines.append(f"Date sold: {sale_date}")

    return lines


def municipality_from_county_zoning(zoning_value: str | None) -> str | None:
    """Extract a municipality name from county zoning ETJ polygons."""
    text = _clean(zoning_value)
    if not text:
        return None
    lower = text.lower()
    if lower in {"ra", "rs", "rr", "rm", "rc", "rg", "cb", "ci", "cii", "cbi cd"}:
        return None
    if any(marker in lower for marker in MUNICIPAL_ETJ_MARKERS):
        return text.replace(" City", "").replace(" city", "").replace(" Town", "").strip()
    if " " not in text and len(text) <= 4:
        return None
    return text

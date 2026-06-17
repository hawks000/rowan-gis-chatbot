"""Tests for tax parcel attribute formatting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.parcel_facts import format_parcel_attribute_lines, municipality_from_county_zoning


def test_format_parcel_attribute_lines():
    row = {
        "CALCACRE": 1.14,
        "TOWNSHIP": "CLEVELAND",
        "TAX_DISTRICT": "CLEVELAND FSD",
        "ZIPCODE": "27013-9187",
        "LANDFMV": 37851,
        "IMP_FMV": 76490,
        "TOT_VAL": 114341,
        "DATESOLD": 1236038400000,
        "SALE_AMT": 24000,
    }
    lines = format_parcel_attribute_lines(row)
    assert any(line.startswith("Acreage:") for line in lines)
    assert any(line.startswith("Township: Cleveland") for line in lines)
    assert any(line.startswith("Tax district:") for line in lines)
    assert any(line.startswith("ZIP: 27013") for line in lines)
    assert any(line.startswith("Land value:") for line in lines)
    assert any(line.startswith("Improvement value:") for line in lines)
    assert any(line.startswith("Total value:") for line in lines)
    assert any(line.startswith("Date sold:") for line in lines)


def test_municipality_from_county_zoning():
    assert municipality_from_county_zoning("Salisbury City") == "Salisbury"
    assert municipality_from_county_zoning("RA") is None


if __name__ == "__main__":
    test_format_parcel_attribute_lines()
    test_municipality_from_county_zoning()
    print("OK (parcel facts formatter)")

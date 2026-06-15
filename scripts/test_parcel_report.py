"""Unit tests for ParcelReport summary formatting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.parcel_report import summarize_parcel_report


def _sample_report() -> dict:
    return {
        "results": [
            {
                "layerName": "City Limits",
                "attributes": {"CITY_NAME": None},
            },
            {
                "layerName": "ALL ZONING",
                "attributes": {"ZONING": "RA"},
            },
            {
                "layerName": "School Attendance Areas",
                "attributes": {
                    "ELEM_School_Name": "West Rowan Elementary",
                    "MIDDLE_School_Name": "West Rowan Middle",
                    "HIGH_School_Name": "West Rowan High",
                },
            },
            {
                "layerName": "Voting Precincts",
                "attributes": {
                    "PRECINCT_NAME": "Cleveland",
                    "PRECINCT_NUMBER": "12",
                },
            },
            {
                "layerName": "FEMA Flood Panel",
                "attributes": {"PANEL": "5712", "FIRM_ID": "3710571200"},
            },
        ]
    }


def run_tests():
    report = _sample_report()

    zoning = summarize_parcel_report(report, focus="zoning")
    assert zoning["lines"] == ["Zoning: RA"]

    property_info = summarize_parcel_report(report, focus="property_info")
    assert "Location: unincorporated Rowan County" in property_info["lines"]
    assert "Zoning: RA" in property_info["lines"]
    assert not any("County zoning" in line for line in property_info["lines"])
    assert not any("Municipal zoning" in line for line in property_info["lines"])

    schools = summarize_parcel_report(report, focus="schools")
    assert schools["lines"]
    assert "West Rowan Elementary" in schools["lines"][0]

    voting = summarize_parcel_report(report, focus="voting")
    assert voting["lines"]
    assert "Cleveland" in voting["lines"][0]

    flood_report = {
        "results": report["results"]
        + [{
            "layerName": "Parcel Flood Status",
            "attributes": {
                "AC_in_Flood": 0.12397,
                "has_flood": True,
                "FID_Flood_Dissolve": 1,
            },
        }]
        + [{
            "layerName": "FEMA Flood Panel",
            "attributes": {"PANEL": "5712", "FIRM_ID": "3710571200"},
        }]
    }
    flood = summarize_parcel_report(flood_report, focus="property_info")
    assert any("Flood on parcel: Yes" in line for line in flood["lines"])

    no_flood = summarize_parcel_report({
        "results": [{
            "layerName": "Parcel Flood Status",
            "attributes": {
                "AC_in_Flood": 0.0,
                "has_flood": False,
                "FID_Flood_Dissolve": -1,
                "parcel_acres_field": 1.14,
            },
        }]
    }, focus="flood")
    assert no_flood["lines"] == ["Flood on parcel: No"]

    empty = summarize_parcel_report({"results": []}, focus="zoning")
    assert "No zoning polygon" in empty["lines"][0]

    print("OK (parcel report formatter)")


if __name__ == "__main__":
    run_tests()

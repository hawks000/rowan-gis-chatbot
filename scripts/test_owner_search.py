"""Tests for owner name search (OWNNAME + OWN2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.owner_search import (
    build_owner_where_clause,
    combined_owner_name,
    extract_owner_tokens,
    owner_matches_query,
)
from services.query_parser import build_where_clause, parse_query
from services.arcgis_client import query_layer


def test_owner_tokens():
    assert extract_owner_tokens("Randy J Cress") == ["RANDY", "J", "CRESS"]
    assert extract_owner_tokens("owner Cress Randy") == ["CRESS", "RANDY"]


def test_combined_owner_name():
    row = {"OWNNAME": "CRESS JOHN M JR  &", "OWN2": "CRESS RANDY J"}
    assert combined_owner_name(row) == "CRESS JOHN M JR & CRESS RANDY J"
    assert owner_matches_query("Cress Randy", row)
    assert owner_matches_query("Randy Cress", row)
    assert owner_matches_query("Randy J Cress", row)


def test_live_cress_search():
    for query in ("Cress Randy", "Randy Cress", "Randy J Cress"):
        intent = parse_query(query)
        assert intent is not None
        assert intent.intent_type == "owner"
        where = build_where_clause(intent)
        data = query_layer(where, result_record_count=10)
        owners = [combined_owner_name(f.get("properties") or {}) for f in data.get("features", [])]
        assert any("RANDY" in owner and "CRESS" in owner for owner in owners), query


if __name__ == "__main__":
    test_owner_tokens()
    test_combined_owner_name()
    test_live_cress_search()
    print("OK (owner search)")

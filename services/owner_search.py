"""Owner name parsing and GIS where-clause helpers for Rowan tax parcels."""

import re
from typing import Any

from services.text_normalize import fuzzy_score

OWNER_FIELDS = ("OWNNAME", "OWN2")

NAME_STOPWORDS = {
    "THE", "AND", "FOR", "OWN", "OWNER", "OWNING", "PROPERTY", "PROPERTIES",
    "PARCEL", "PARCELS", "FIND", "SEARCH", "SHOW", "LIST", "ALL", "ANY",
    "WHO", "NAME", "NC", "ROWAN", "COUNTY",
}

SUFFIX_TOKENS = {"JR", "SR", "II", "III", "IV", "V"}


def extract_owner_tokens(value: str) -> list[str]:
    """Pull searchable tokens from a person-name query."""
    raw_tokens = re.findall(r"[A-Za-z0-9]+", value.upper())
    tokens: list[str] = []
    for token in raw_tokens:
        if token in NAME_STOPWORDS:
            continue
        if token in SUFFIX_TOKENS:
            tokens.append(token)
            continue
        if len(token) >= 2:
            tokens.append(token)
            continue
        if len(token) == 1 and token.isalpha() and tokens:
            tokens.append(token)
    return tokens


def _escape(value: str) -> str:
    return value.replace("'", "''")


def _field_like_clause(field: str, token: str) -> str:
    return f"UPPER({field}) LIKE '%{_escape(token)}%'"


def _all_tokens_in_field(field: str, tokens: list[str]) -> str:
    return " AND ".join(_field_like_clause(field, token) for token in tokens)


def build_owner_where_clause(value: str) -> str:
    """
    Build a WHERE clause that matches Rowan owner fields.

    Rowan stores the primary owner in OWNNAME and co-owners in OWN2, usually
    as LAST FIRST MIDDLE (e.g. OWNNAME='CRESS JOHN M JR  &', OWN2='CRESS RANDY J').
    """
    cleaned = re.sub(r"^(?:owner|owned by)\s+", "", value.strip(), flags=re.I)
    tokens = extract_owner_tokens(cleaned)
    if not tokens:
        token = _escape(cleaned.upper())
        return " OR ".join(f"UPPER({field}) LIKE '%{token}%'" for field in OWNER_FIELDS)

    clauses: list[str] = []

    for field in OWNER_FIELDS:
        clauses.append(f"({_all_tokens_in_field(field, tokens)})")

    cross_field = " AND ".join(
        "(" + " OR ".join(_field_like_clause(field, token) for field in OWNER_FIELDS) + ")"
        for token in tokens
    )
    clauses.append(f"({cross_field})")

    if len(tokens) >= 2:
        last, first = tokens[0], tokens[1]
        for field in OWNER_FIELDS:
            clauses.append(
                f"(UPPER({field}) LIKE '%{_escape(last)}%' "
                f"AND UPPER({field}) LIKE '%{_escape(first)}%')"
            )
            clauses.append(
                f"(UPPER({field}) LIKE '%{_escape(first)}%' "
                f"AND UPPER({field}) LIKE '%{_escape(last)}%')"
            )

    return "(" + " OR ".join(dict.fromkeys(clauses)) + ")"


def combined_owner_name(row: dict[str, Any]) -> str:
    """Return a display-friendly owner string from parcel attributes."""
    primary = str(row.get("OWNNAME") or "").strip().rstrip("&").strip()
    secondary = str(row.get("OWN2") or "").strip()
    if primary and secondary:
        return f"{primary} & {secondary}"
    return primary or secondary or "Unknown owner"


def owner_record_text(row: dict[str, Any]) -> str:
    """All owner text used for fuzzy scoring."""
    parts = [str(row.get(field) or "").strip() for field in OWNER_FIELDS]
    return " ".join(part for part in parts if part)


def owner_matches_query(query: str, row: dict[str, Any], *, min_ratio: float = 0.58) -> bool:
    """Return True when a parcel row matches a natural-language owner query."""
    tokens = extract_owner_tokens(query)
    owner_text = owner_record_text(row).upper()
    if not tokens or not owner_text:
        return False

    if all(token in owner_text for token in tokens):
        return True

    if len(tokens) >= 2:
        last, first = tokens[0], tokens[1]
        if last in owner_text and first in owner_text:
            return True
        if fuzzy_score(f"{first} {last}", owner_text) >= min_ratio:
            return True
        if fuzzy_score(f"{last} {first}", owner_text) >= min_ratio:
            return True

    return fuzzy_score(query, owner_text) >= min_ratio

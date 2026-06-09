"""Rule-based natural language → GIS query intent parser (Phase 1, no LLM)."""

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class QueryIntent:
    intent_type: str
    field: str
    value: str
    layer: str = "RowanTaxParcels"
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PARCEL_LABEL_PATTERN = re.compile(
    r"^(?:parcel(?:\s+id)?|pin|tax\s+id)\s*[:#]?\s*(.+)$",
    re.IGNORECASE,
)
PIN_PATTERN = re.compile(
    r"\b(?:pin|parcel(?:\s+(?:id|#|number))?|tax\s+id)\s*[:#]?\s*([A-Za-z0-9\-]+)\b",
    re.IGNORECASE,
)
PIN_FULL_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2}-\d{4})\b", re.IGNORECASE)
PIN_PARTIAL_PATTERN = re.compile(r"\b(\d{2,4}[\s\-]\d{2,4}(?:[\s\-]\d{2,4})*)\b")

WHO_OWNS_PATTERN = re.compile(r"who owns\s+(.+)", re.IGNORECASE)
FIND_OWNING_PATTERN = re.compile(
    r"(?:find|search for|look up|lookup)\s+(.+?)\s+owning(?:\s+property|\s+properties)?\s*$",
    re.IGNORECASE,
)
OWNER_PATTERN = re.compile(
    r"(?:owned\s+by|owner(?:\s+name)?|find\s+(?:parcels?\s+)?(?:owned\s+by|for))\s+(.+)",
    re.IGNORECASE,
)
OWNER_SHORT_PATTERN = re.compile(r"^owner\s+(.+)$", re.IGNORECASE)

STREET_PATTERN = re.compile(
    r"(?:parcels?\s+on|show\s+(?:me\s+)?(?:parcels?\s+on|on)|street)\s+(.+)",
    re.IGNORECASE,
)

ADDRESS_PATTERN = re.compile(
    r"(?:address|located\s+at|property\s+at|where\s+is)\s+(.+)",
    re.IGNORECASE,
)

PREFIX_STRIPS = [
    re.compile(r"^(?:can you |please )+", re.IGNORECASE),
    re.compile(r"^(?:find|search for|look up|lookup|show me|tell me about|get)\s+", re.IGNORECASE),
]

STOPWORDS = {
    "THE", "A", "AN", "OF", "FOR", "AND", "OR", "TO", "IN", "AT",
    "PROPERTY", "PROPERTIES", "PARCEL", "PARCELS", "NC", "NORTH",
    "CAROLINA", "ROWAN", "COUNTY", "YOU", "ME", "MY", "ANY", "ALL",
    "PARCEL", "PIN", "ID", "NUMBER", "NUM", "TAX",
}

STREET_TOKENS = (
    "st", "street", "rd", "road", "dr", "drive", "ln", "lane", "mt", "mount",
    "ave", "avenue", "blvd", "court", "ct", "way", "circle", "cir", "trl", "trail",
    "hwy", "highway", "pkwy", "parkway", "pl", "place",
)


def _clean_value(value: str) -> str:
    cleaned = value.strip().strip("?.!,")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _strip_prefixes(text: str) -> str:
    cleaned = text.strip()
    changed = True
    while changed:
        changed = False
        for pattern in PREFIX_STRIPS:
            updated = pattern.sub("", cleaned).strip()
            if updated != cleaned:
                cleaned = updated
                changed = True
    return cleaned


def _extract_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", value.upper())
    return [token for token in tokens if len(token) >= 2 and token not in STOPWORDS]


def _extract_parcel_id(value: str) -> str | None:
    """Pull a parcel/PIN identifier out of free text."""
    cleaned = _clean_value(value)
    label_match = PARCEL_LABEL_PATTERN.match(cleaned)
    if label_match:
        return _clean_value(label_match.group(1))

    full_match = PIN_FULL_PATTERN.search(cleaned)
    if full_match:
        return full_match.group(1)

    if re.search(r"\b(?:pin|parcel|tax\s+id)\b", cleaned, re.IGNORECASE):
        pin_match = PIN_PATTERN.search(cleaned)
        if pin_match:
            return _clean_value(pin_match.group(1))

    partial_match = PIN_PARTIAL_PATTERN.search(cleaned)
    if partial_match:
        return _clean_value(partial_match.group(1))

    if re.fullmatch(r"[\d\s\-]+", cleaned) and re.search(r"\d", cleaned):
        return cleaned

    return None


def _looks_like_parcel_id(text: str) -> bool:
    return _extract_parcel_id(text) is not None


def _looks_like_address(text: str) -> bool:
    if _looks_like_parcel_id(text):
        return False

    lower = text.lower()
    if any(re.search(rf"\b{re.escape(token)}\b", lower) for token in STREET_TOKENS):
        return True

    # Number + name pattern (e.g. 550 MT HALL RD) — not bare numeric groups like 561 023
    if re.search(r"\d+\s+[A-Za-z]", text):
        return True

    return False


def _looks_like_owner_name(text: str) -> bool:
    if _looks_like_parcel_id(text) or _looks_like_address(text):
        return False
    if re.match(r"^\d", text):
        return False
    tokens = _extract_tokens(text)
    return len(tokens) >= 1


def _intent_for_subject(value: str, prefix: str = "") -> QueryIntent:
    """Classify a subject string into pin, address, or owner intent."""
    cleaned = _clean_value(value)
    label = prefix or cleaned

    parcel_id = _extract_parcel_id(cleaned)
    if parcel_id:
        return QueryIntent(
            intent_type="pin",
            field="PIN",
            value=parcel_id.upper(),
            description=f"Parcel lookup for {parcel_id}",
        )

    if _looks_like_address(cleaned):
        return QueryIntent(
            intent_type="address",
            field="PROP_ADDRESS",
            value=cleaned,
            description=f"Address lookup for {cleaned}",
        )

    return QueryIntent(
        intent_type="owner",
        field="OWNNAME",
        value=cleaned,
        description=f"Parcels owned by {cleaned}",
    )


def parse_query(message: str) -> QueryIntent | None:
    """Return structured intent or None if the message cannot be parsed."""
    text = message.strip()
    if not text:
        return None

    who_owns = WHO_OWNS_PATTERN.search(text)
    if who_owns:
        return _intent_for_subject(who_owns.group(1))

    stripped = _strip_prefixes(text)

    find_owning = FIND_OWNING_PATTERN.search(stripped) or FIND_OWNING_PATTERN.search(text)
    if find_owning:
        value = _clean_value(find_owning.group(1))
        if len(value) >= 2:
            return QueryIntent(
                intent_type="owner",
                field="OWNNAME",
                value=value,
                description=f"Parcels owned by {value}",
            )

    owner_match = OWNER_PATTERN.search(stripped) or OWNER_SHORT_PATTERN.search(stripped)
    if owner_match:
        value = _clean_value(owner_match.group(1))
        if len(value) >= 2:
            return _intent_for_subject(value)

    street_match = STREET_PATTERN.search(stripped)
    if street_match:
        value = _clean_value(street_match.group(1))
        if len(value) >= 3:
            return QueryIntent(
                intent_type="street",
                field="ST_NAME",
                value=value,
                description=f"Parcels on {value}",
            )

    address_match = ADDRESS_PATTERN.search(stripped)
    if address_match:
        value = _clean_value(address_match.group(1))
        if len(value) >= 3:
            return _intent_for_subject(value)

    pin_match = PIN_PATTERN.search(stripped)
    if pin_match:
        value = _clean_value(pin_match.group(1))
        return QueryIntent(
            intent_type="pin",
            field="PIN",
            value=value.upper(),
            description=f"Parcel lookup for {value}",
        )

    parcel_id = _extract_parcel_id(stripped)
    if parcel_id:
        return QueryIntent(
            intent_type="pin",
            field="PIN",
            value=parcel_id.upper(),
            description=f"Parcel lookup for {parcel_id}",
        )

    subject = _clean_value(stripped)
    if len(subject) >= 3:
        return _intent_for_subject(subject)

    return None


def _parcel_id_variants(value: str) -> list[str]:
    """Generate common PIN / PARCEL_ID formatting variants."""
    upper = value.upper().strip()
    variants = {upper, upper.replace(" ", "-"), upper.replace("-", " "), re.sub(r"[\s\-]", "", upper)}

    parts = re.findall(r"\d+", upper)
    if len(parts) >= 2:
        variants.add("-".join(parts))
        variants.add(" ".join(parts))
        variants.add("".join(parts))
        variants.add(f"{parts[0]}-{parts[1]}")
        variants.add(f"{parts[0]} {parts[1]}")

    return [variant for variant in variants if variant]


def build_where_clause(intent: QueryIntent) -> str:
    """Build ArcGIS SQL WHERE clause from parsed intent."""
    value = intent.value.replace("'", "''")

    if intent.intent_type == "pin":
        clauses = []
        for variant in _parcel_id_variants(value):
            escaped = variant.replace("'", "''")
            clauses.append(f"UPPER(PIN) = '{escaped}'")
            clauses.append(f"UPPER(PARCEL_ID) = '{escaped}'")

        compact = re.sub(r"[^A-Z0-9]", "", value.upper())
        parts = re.findall(r"\d+", value)
        if len(parts) >= 2 and len(compact) <= 12:
            p0, p1 = parts[0], parts[1]
            clauses.append(f"UPPER(PIN) LIKE '%{p0}%{p1}%'")
            clauses.append(f"UPPER(PARCEL_ID) LIKE '%{p0}%{p1}%'")
            clauses.append(f"UPPER(PIN) LIKE '%{p0}-{p1}%'")
            clauses.append(f"UPPER(PARCEL_ID) LIKE '%{p0}-{p1}%'")

        unique = list(dict.fromkeys(clauses))
        return f"({' OR '.join(unique)})"

    if intent.intent_type == "owner":
        tokens = _extract_tokens(value)
        if not tokens:
            token = value.upper()
            return f"UPPER(OWNNAME) LIKE '%{token}%'"
        clauses = [f"UPPER(OWNNAME) LIKE '%{token}%'" for token in tokens]
        return " AND ".join(clauses)

    if intent.intent_type == "street":
        tokens = _extract_tokens(value)
        if not tokens:
            token = value.upper()
            return (
                f"(UPPER(ST_NAME) LIKE '%{token}%' OR UPPER(PHYSSTREET) LIKE '%{token}%' "
                f"OR UPPER(PROP_ADDRESS) LIKE '%{token}%')"
            )
        field_clauses = []
        for token in tokens:
            field_clauses.append(
                f"(UPPER(ST_NAME) LIKE '%{token}%' OR UPPER(PHYSSTREET) LIKE '%{token}%' "
                f"OR UPPER(PROP_ADDRESS) LIKE '%{token}%')"
            )
        return " AND ".join(field_clauses)

    if intent.intent_type == "address":
        tokens = _extract_tokens(value)
        if not tokens:
            token = value.upper()
            return (
                f"(UPPER(PROP_ADDRESS) LIKE '%{token}%' OR UPPER(TAXADD1) LIKE '%{token}%' "
                f"OR UPPER(PHYSSTREET) LIKE '%{token}%')"
            )
        field_clauses = []
        for token in tokens:
            field_clauses.append(
                f"(UPPER(PROP_ADDRESS) LIKE '%{token}%' OR UPPER(TAXADD1) LIKE '%{token}%' "
                f"OR UPPER(PHYSSTREET) LIKE '%{token}%')"
            )
        return " AND ".join(field_clauses)

    raise ValueError(f"Unsupported intent type: {intent.intent_type}")

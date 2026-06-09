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


PIN_PATTERN = re.compile(
    r"\b(?:pin|parcel(?:\s+(?:id|#|number))?|tax\s+id)\s*[:#]?\s*([A-Za-z0-9\-]+)\b",
    re.IGNORECASE,
)
PIN_BARE_PATTERN = re.compile(r"\b(\d{8,12}[A-Za-z]?)\b")

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
}


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


def _looks_like_address(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\d+\s+\w+", text):
        return True
    street_tokens = (
        "st", "street", "rd", "road", "dr", "drive", "ln", "lane", "mt", "mount",
        "ave", "avenue", "blvd", "court", "ct", "way", "circle", "cir", "trl", "trail",
    )
    return any(re.search(rf"\b{re.escape(token)}\b", lower) for token in street_tokens)


def _looks_like_owner_name(text: str) -> bool:
    if _looks_like_address(text):
        return False
    if re.match(r"^\d", text):
        return False
    tokens = _extract_tokens(text)
    return len(tokens) >= 1 and all(re.search(r"[A-Z]", t, re.IGNORECASE) for t in tokens)


def parse_query(message: str) -> QueryIntent | None:
    """Return structured intent or None if the message cannot be parsed."""
    text = message.strip()
    if not text:
        return None

    who_owns = WHO_OWNS_PATTERN.search(text)
    if who_owns:
        value = _clean_value(who_owns.group(1))
        if _looks_like_address(value):
            return QueryIntent(
                intent_type="address",
                field="PROP_ADDRESS",
                value=value,
                description=f"Address lookup for {value}",
            )
        return QueryIntent(
            intent_type="owner",
            field="OWNNAME",
            value=value,
            description=f"Parcels owned by {value}",
        )

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
            return QueryIntent(
                intent_type="owner",
                field="OWNNAME",
                value=value,
                description=f"Parcels owned by {value}",
            )

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
            return QueryIntent(
                intent_type="address",
                field="PROP_ADDRESS",
                value=value,
                description=f"Address lookup for {value}",
            )

    pin_match = PIN_PATTERN.search(stripped)
    if pin_match:
        value = _clean_value(pin_match.group(1))
        return QueryIntent(
            intent_type="pin",
            field="PIN",
            value=value.upper(),
            description=f"Parcel PIN lookup for {value}",
        )

    if re.search(r"\bpin\b", stripped, re.IGNORECASE):
        bare = PIN_BARE_PATTERN.search(stripped)
        if bare:
            value = bare.group(1)
            return QueryIntent(
                intent_type="pin",
                field="PIN",
                value=value.upper(),
                description=f"Parcel PIN lookup for {value}",
            )

    subject = _clean_value(stripped)
    if _looks_like_address(subject) and len(subject) >= 5:
        return QueryIntent(
            intent_type="address",
            field="PROP_ADDRESS",
            value=subject,
            description=f"Address lookup for {subject}",
        )

    if _looks_like_owner_name(subject):
        return QueryIntent(
            intent_type="owner",
            field="OWNNAME",
            value=subject,
            description=f"Parcels owned by {subject}",
        )

    if len(subject) >= 3:
        return QueryIntent(
            intent_type="owner",
            field="OWNNAME",
            value=subject,
            description=f"Parcels owned by {subject}",
        )

    return None


def build_where_clause(intent: QueryIntent) -> str:
    """Build ArcGIS SQL WHERE clause from parsed intent."""
    value = intent.value.replace("'", "''")

    if intent.intent_type == "pin":
        pin = value.upper()
        return f"(UPPER(PIN) = '{pin}' OR UPPER(PARCEL_ID) = '{pin}')"

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

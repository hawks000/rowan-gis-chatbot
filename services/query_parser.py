"""Rule-based natural language → GIS query intent parser (Phase 1, no LLM)."""

import re
from dataclasses import asdict, dataclass
from typing import Any

from services.text_normalize import (
    detect_context_focus,
    extract_address_fragment,
    extract_pin_from_text,
    extract_search_subject,
    normalize_query_text,
    strip_question_wrapper,
    with_city_from_message,
)


@dataclass
class QueryIntent:
    intent_type: str
    field: str
    value: str
    layer: str = "RowanTaxParcels"
    description: str = ""
    context_focus: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PARCEL_LABEL_PATTERN = re.compile(
    r"^(?:parcel(?:\s+id)?|pin|tax\s+id)\s*[:#]?\s*(.+)$",
    re.IGNORECASE,
)
PIN_PATTERN = re.compile(
    r"\b(?:pin|parcel(?:\s+(?:id|#|number))?|tax\s+id)\s*[:#]?\s*"
    r"(\d{4}-\d{2}-\d{2}-\d{4}|\d{1,4}(?:[\s\-]\d{1,7})+|\d+)\b",
    re.IGNORECASE,
)
PIN_FULL_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2}-\d{4})\b", re.IGNORECASE)
PIN_PARTIAL_PATTERN = re.compile(r"\b(\d{2,4}[\s\-]\d{2,4}(?:[\s\-]\d{2,4})*)\b")
ROWAN_TAX_PARCEL_ID_PATTERN = re.compile(
    r"\b(?:parcel(?:\s+id)?|parcel)\s+(\d{1,4}\s+\d{1,7})\b",
    re.IGNORECASE,
)

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

STREET_PARCEL_COUNT_PATTERN = re.compile(
    r"how many parcels?(?: are)?(?: on| along| in)?\s+(.+?)\??$",
    re.IGNORECASE,
)
STREET_HOUSE_COUNT_PATTERN = re.compile(
    r"how many (?:houses|homes|addresses|address points?)(?: are)?(?: on| along| in)?\s+(.+?)\??$",
    re.IGNORECASE,
)
STREET_PARCEL_PATTERN = re.compile(
    r"(?:parcels?(?:\s+on|\s+along|\s+in)?|show\s+(?:me\s+)?(?:parcels?\s+on|on))\s+(.+)",
    re.IGNORECASE,
)

SUBDIVISION_ADDRESS_COUNT_PATTERN = re.compile(
    r"how many (?:addresses|houses|homes|address points?)(?: are)? in (?:subdivision|sub(?:division)?)\s+(.+?)\??$",
    re.IGNORECASE,
)
SUBDIVISION_PARCEL_PATTERN = re.compile(
    r"(?:(?:who owns|find|show)(?:\s+all)?\s+(?:the\s+)?parcels?(?:\s+owned)?|parcels?(?:\s+owned)?|properties)"
    r"(?:\s+in|\s+inside|\s+within)\s+(?:subdivision|sub(?:division)?)\s+(.+?)\??$",
    re.IGNORECASE,
)

ADDRESS_PATTERN = re.compile(
    r"(?:address|located\s+at|property\s+at|where\s+is)\s+(.+)",
    re.IGNORECASE,
)

PROPERTY_CONTEXT_PATTERN = re.compile(
    r"^(?:what(?:'s|\s+is|\s+are)\s+(?:the\s+)?)?"
    r"(?:zoning|flood(?:\s+zone|\s+panel|\s+info)?|fema(?:\s+flood)?|school(?:s)?|soil(?:s)?|"
    r"voting(?:\s+precinct)?|watershed|property(?:\s+info|\s+details|\s+report)?)"
    r"\s+(?:for|at|on|of|near)\s+(.+)$",
    re.IGNORECASE,
)

PREFIX_STRIPS = [
    re.compile(r"^(?:can you |please )+", re.IGNORECASE),
    re.compile(r"^(?:find|search for|look up|lookup|show me|tell me about|get|list)\s+", re.IGNORECASE),
]

STOPWORDS = {
    "THE", "A", "AN", "OF", "FOR", "AND", "OR", "TO", "IN", "AT", "ON", "ALONG",
    "PROPERTY", "PROPERTIES", "PARCEL", "PARCELS", "NC", "NORTH",
    "CAROLINA", "ROWAN", "COUNTY", "YOU", "ME", "MY", "ANY", "ALL",
    "PIN", "ID", "NUMBER", "NUM", "TAX", "SUBDIVISION", "SUB",
}

STREET_TOKENS = (
    "st", "street", "rd", "road", "dr", "drive", "ln", "lane", "mt", "mount",
    "ave", "avenue", "blvd", "court", "ct", "way", "circle", "cir", "trl", "trail",
    "hwy", "highway", "pkwy", "parkway", "pl", "place",
)


def _clean_value(value: str) -> str:
    cleaned = normalize_query_text(value.strip().strip("?.!,"))
    return re.sub(r"\s+", " ", cleaned)


def _resolve_subject(value: str, *, full_message: str = "") -> str:
    """Normalize noisy natural-language input down to an address, name, or PIN subject."""
    source = full_message or value
    subject, hint = extract_search_subject(source)
    if subject and hint in {"address", "pin", "street", "name"}:
        return subject
    return _clean_value(strip_question_wrapper(value))


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


def _is_tax_parcel_id_format(value: str) -> bool:
    """Rowan tax PARCEL_ID format, e.g. '304 157' (not the dashed PIN)."""
    cleaned = _clean_value(value)
    if _is_full_rowan_pin(cleaned):
        return False
    return bool(re.fullmatch(r"\d{1,4}\s+\d{1,7}", cleaned))


def extract_tax_parcel_id(text: str) -> str | None:
    """Extract Rowan tax parcel id like '304 157' from natural language."""
    normalized = _clean_value(text)
    label_match = PARCEL_LABEL_PATTERN.match(normalized)
    if label_match:
        candidate = _clean_value(label_match.group(1))
        if _is_tax_parcel_id_format(candidate) or PIN_FULL_PATTERN.search(candidate):
            return candidate

    inline = ROWAN_TAX_PARCEL_ID_PATTERN.search(normalized)
    if inline:
        return _clean_value(inline.group(1))

    if re.search(r"\b(?:pin|parcel|tax\s+id)\b", normalized, re.IGNORECASE):
        pin_match = PIN_PATTERN.search(normalized)
        if pin_match:
            return _clean_value(pin_match.group(1))

    partial_match = PIN_PARTIAL_PATTERN.search(normalized)
    if partial_match:
        candidate = _clean_value(partial_match.group(1))
        if _is_tax_parcel_id_format(candidate):
            return candidate

    return None


def _parcel_lookup_field(value: str) -> str:
    return "PARCEL_ID" if _is_tax_parcel_id_format(value) else "PIN"


def _parcel_lookup_intent(
    value: str,
    *,
    context_focus: str = "",
    description: str | None = None,
) -> QueryIntent:
    cleaned = _clean_value(value)
    field = _parcel_lookup_field(cleaned)
    return QueryIntent(
        intent_type="pin",
        field=field,
        value=cleaned.upper() if field == "PIN" else cleaned,
        description=description or f"Parcel lookup for {cleaned}",
        context_focus=context_focus,
    )


def _extract_parcel_id(value: str) -> str | None:
    cleaned = _clean_value(value)
    tax_id = extract_tax_parcel_id(cleaned)
    if tax_id:
        return tax_id

    full_match = PIN_FULL_PATTERN.search(cleaned)
    if full_match:
        return full_match.group(1)

    partial_match = PIN_PARTIAL_PATTERN.search(cleaned)
    if partial_match:
        return _clean_value(partial_match.group(1))

    if re.fullmatch(r"[\d\s\-]+", cleaned) and re.search(r"\d", cleaned):
        return cleaned

    return None


def _looks_like_parcel_id(text: str) -> bool:
    return _extract_parcel_id(text) is not None


def _looks_like_subdivision_reference(text: str) -> bool:
    lower = text.lower()
    return "subdivision" in lower or re.search(r"\bsub(?:division)?\b", lower)


def _looks_like_address(text: str) -> bool:
    if _looks_like_parcel_id(text) or _looks_like_subdivision_reference(text):
        return False
    lower = text.lower()
    if any(re.search(rf"\b{re.escape(token)}\b", lower) for token in STREET_TOKENS):
        return True
    return bool(re.search(r"\d+\s+[A-Za-z]", text))


def _extract_subdivision_name(text: str) -> str | None:
    for pattern in (
        SUBDIVISION_ADDRESS_COUNT_PATTERN,
        SUBDIVISION_PARCEL_PATTERN,
        re.compile(r"(?:in|inside|within)\s+(?:subdivision|sub(?:division)?)\s+(.+)", re.I),
    ):
        match = pattern.search(text)
        if match:
            return _clean_value(match.group(1))
    return None


def _intent_for_subject(value: str, *, context_focus: str = "", full_message: str = "") -> QueryIntent:
    message = full_message or value
    cleaned = _resolve_subject(value, full_message=message)
    focus = context_focus or detect_context_focus(message)

    subdivision = _extract_subdivision_name(cleaned) or _extract_subdivision_name(message)
    if subdivision:
        if re.search(r"\b(?:address|house|home)s?\b", value, re.I):
            return QueryIntent(
                intent_type="subdivision_addresses",
                field="SUBNAME",
                value=subdivision,
                layer="Public/search/MapServer/3",
                description=f"Addresses in subdivision {subdivision}",
            )
        return QueryIntent(
            intent_type="subdivision_parcels",
            field="SUBNAME",
            value=subdivision,
            layer="Public/search/MapServer/3",
            description=f"Parcels in subdivision {subdivision}",
        )

    parcel_id = _extract_parcel_id(cleaned) or extract_tax_parcel_id(message) or extract_pin_from_text(message)
    if parcel_id:
        return _parcel_lookup_intent(
            parcel_id,
            context_focus=focus,
        )

    if (
        _looks_like_address(cleaned)
        or extract_address_fragment(message)
        or extract_address_fragment(value)
    ):
        extracted, hint = extract_search_subject(message or value)
        if hint in {"address", "pin", "street"}:
            subject = extracted
        else:
            subject = extract_address_fragment(message) or extract_address_fragment(value) or cleaned
        subject = with_city_from_message(subject, message)
        description = f"Address lookup for {subject}"
        if focus == "zoning":
            description = f"Zoning lookup for {subject}"
        elif focus == "flood":
            description = f"Flood zone lookup for {subject}"
        elif focus == "schools":
            description = f"School district lookup for {subject}"
        elif focus == "property_info":
            description = f"Property context for {subject}"
        return QueryIntent(
            intent_type="address",
            field="Address",
            value=subject,
            layer="Public/search/MapServer/0",
            description=description,
            context_focus=focus,
        )

    return QueryIntent(
        intent_type="owner",
        field="OWNNAME",
        value=cleaned,
        description=f"Parcels owned by {cleaned}",
    )


def _intent_from_freeform(message: str) -> QueryIntent | None:
    """Last-resort parser: pull address/PIN/name out of any phrasing."""
    focus = detect_context_focus(message)
    subject, hint = extract_search_subject(message)
    if not subject:
        return None

    if hint == "pin":
        return _parcel_lookup_intent(subject, context_focus=focus)

    if hint in {"address", "street"} or extract_address_fragment(message):
        subject = extract_address_fragment(message) or subject
        subject = with_city_from_message(subject, message)
        description = f"Address lookup for {subject}"
        if focus == "zoning":
            description = f"Zoning lookup for {subject}"
        elif focus == "flood":
            description = f"Flood zone lookup for {subject}"
        elif focus == "schools":
            description = f"School district lookup for {subject}"
        elif focus == "property_info":
            description = f"Property context for {subject}"
        return QueryIntent(
            intent_type="address",
            field="Address",
            value=subject,
            layer="Public/search/MapServer/0",
            description=description,
            context_focus=focus,
        )

    if focus:
        return _intent_for_subject(subject, context_focus=focus, full_message=message)

    return _intent_for_subject(subject, full_message=message)


def retry_intent_from_message(message: str, original: QueryIntent) -> QueryIntent | None:
    """If the first parse/query failed, try a subject extracted from the raw message."""
    retry = _intent_from_freeform(message)
    if not retry:
        return None
    if retry.intent_type == original.intent_type and retry.value == original.value:
        return None
    if retry.value == original.value:
        return None
    return retry


def parse_query(message: str) -> QueryIntent | None:
    text = normalize_query_text(message.strip())
    if not text:
        return None

    stripped = _strip_prefixes(text)
    focus = detect_context_focus(text)

    # Fast path: context question with embedded address/PIN anywhere in the message.
    embedded_address = extract_address_fragment(text)
    embedded_pin = extract_pin_from_text(text)
    embedded_parcel_id = extract_tax_parcel_id(text)
    if focus and (embedded_address or embedded_pin or embedded_parcel_id):
        if embedded_parcel_id:
            return _parcel_lookup_intent(embedded_parcel_id, context_focus=focus)
        if embedded_pin:
            return _parcel_lookup_intent(embedded_pin, context_focus=focus)
        address_subject = with_city_from_message(embedded_address, text)
        return _intent_for_subject(address_subject, context_focus=focus, full_message=text)

    if embedded_address and not focus:
        quick = _intent_for_subject(embedded_address, full_message=text)
        if quick.intent_type == "address":
            return quick

    subdivision_addr = SUBDIVISION_ADDRESS_COUNT_PATTERN.search(stripped)
    if subdivision_addr:
        value = _clean_value(subdivision_addr.group(1))
        return QueryIntent(
            intent_type="subdivision_addresses",
            field="SUBNAME",
            value=value,
            layer="Public/search/MapServer/3",
            description=f"Addresses in subdivision {value}",
        )

    subdivision_parcels = SUBDIVISION_PARCEL_PATTERN.search(stripped)
    if subdivision_parcels:
        value = _clean_value(subdivision_parcels.group(1))
        return QueryIntent(
            intent_type="subdivision_parcels",
            field="SUBNAME",
            value=value,
            layer="Public/search/MapServer/3",
            description=f"Parcels in subdivision {value}",
        )

    street_houses = STREET_HOUSE_COUNT_PATTERN.search(stripped)
    if street_houses:
        value = _clean_value(street_houses.group(1))
        return QueryIntent(
            intent_type="street_houses",
            field="Whole_Name",
            value=value,
            layer="Public/search/MapServer/1",
            description=f"Addresses/houses on {value}",
        )

    street_parcels_count = STREET_PARCEL_COUNT_PATTERN.search(stripped)
    if street_parcels_count:
        value = _clean_value(street_parcels_count.group(1))
        return QueryIntent(
            intent_type="street_parcels",
            field="ST_NAME",
            value=value,
            description=f"Parcels on {value}",
        )

    who_owns = WHO_OWNS_PATTERN.search(text)
    if who_owns:
        return _intent_for_subject(who_owns.group(1), full_message=text)

    find_owning = FIND_OWNING_PATTERN.search(stripped) or FIND_OWNING_PATTERN.search(text)
    if find_owning:
        value = _clean_value(find_owning.group(1))
        return QueryIntent(
            intent_type="owner",
            field="OWNNAME",
            value=value,
            description=f"Parcels owned by {value}",
        )

    owner_match = OWNER_PATTERN.search(stripped) or OWNER_SHORT_PATTERN.search(stripped)
    if owner_match:
        return _intent_for_subject(owner_match.group(1), full_message=text)

    street_match = STREET_PARCEL_PATTERN.search(stripped)
    if street_match:
        value = _clean_value(street_match.group(1))
        return QueryIntent(
            intent_type="street_parcels",
            field="ST_NAME",
            value=value,
            description=f"Parcels on {value}",
        )

    address_match = ADDRESS_PATTERN.search(stripped)
    if address_match:
        return _intent_for_subject(address_match.group(1), full_message=text)

    pin_match = PIN_PATTERN.search(stripped)
    if pin_match:
        return _parcel_lookup_intent(_clean_value(pin_match.group(1)), context_focus=focus)

    parcel_id = _extract_parcel_id(stripped) or extract_tax_parcel_id(text)
    if parcel_id:
        return _parcel_lookup_intent(parcel_id, context_focus=focus)

    if len(stripped) >= 3:
        intent = _intent_for_subject(stripped, full_message=text)
        if intent.intent_type != "owner" or not focus:
            return intent
        return intent

    return _intent_from_freeform(text)


def _is_full_rowan_pin(value: str) -> bool:
    """Rowan PINs use four numeric groups, e.g. 5733-04-51-7482."""
    return bool(PIN_FULL_PATTERN.search(value.upper().strip()))


def _parcel_id_variants(value: str) -> list[str]:
    upper = value.upper().strip()
    variants = {upper, upper.replace(" ", "-"), upper.replace("-", " "), re.sub(r"[\s\-]", "", upper)}
    parts = re.findall(r"\d+", upper)
    if _is_full_rowan_pin(upper):
        return [variant for variant in variants if variant]

    if len(parts) >= 2:
        variants.update({"-".join(parts), " ".join(parts), f"{parts[0]}-{parts[1]}"})
    return [variant for variant in variants if variant]


def build_where_clause(intent: QueryIntent) -> str:
    value = intent.value.replace("'", "''")

    if intent.intent_type == "pin":
        clauses = []
        for variant in _parcel_id_variants(value):
            escaped = variant.replace("'", "''")
            clauses.append(f"UPPER(PIN) = '{escaped}'")
            clauses.append(f"UPPER(PARCEL_ID) = '{escaped}'")
        if not _is_full_rowan_pin(value) and not _is_tax_parcel_id_format(value):
            parts = re.findall(r"\d+", value)
            if len(parts) >= 2 and len(re.sub(r"[^A-Z0-9]", "", value.upper())) <= 12:
                p0, p1 = parts[0], parts[1]
                clauses.extend([
                    f"UPPER(PIN) LIKE '%{p0}%{p1}%'",
                    f"UPPER(PARCEL_ID) LIKE '%{p0}%{p1}%'",
                ])
        return f"({' OR '.join(dict.fromkeys(clauses))})"

    if intent.intent_type == "owner":
        tokens = _extract_tokens(value)
        if not tokens:
            return f"UPPER(OWNNAME) LIKE '%{value.upper()}%'"
        if len(tokens) >= 2:
            return " AND ".join(f"UPPER(OWNNAME) LIKE '%{token}%'" for token in tokens)
        token = tokens[0]
        return f"UPPER(OWNNAME) LIKE '%{token}%'"

    if intent.intent_type in {"street_parcels", "street"}:
        tokens = _extract_tokens(value)
        if not tokens:
            token = value.upper()
            return (
                f"(UPPER(ST_NAME) LIKE '%{token}%' OR UPPER(PHYSSTREET) LIKE '%{token}%' "
                f"OR UPPER(PROP_ADDRESS) LIKE '%{token}%')"
            )
        return " AND ".join(
            f"(UPPER(ST_NAME) LIKE '%{token}%' OR UPPER(PHYSSTREET) LIKE '%{token}%' "
            f"OR UPPER(PROP_ADDRESS) LIKE '%{token}%')"
            for token in tokens
        )

    if intent.intent_type == "address":
        tokens = _extract_tokens(value)
        if not tokens:
            token = value.upper()
            return (
                f"(UPPER(PROP_ADDRESS) LIKE '%{token}%' OR UPPER(TAXADD1) LIKE '%{token}%' "
                f"OR UPPER(PHYSSTREET) LIKE '%{token}%')"
            )
        return " AND ".join(
            f"(UPPER(PROP_ADDRESS) LIKE '%{token}%' OR UPPER(TAXADD1) LIKE '%{token}%' "
            f"OR UPPER(PHYSSTREET) LIKE '%{token}%')"
            for token in tokens
        )

    raise ValueError(f"Unsupported intent type for SQL where: {intent.intent_type}")

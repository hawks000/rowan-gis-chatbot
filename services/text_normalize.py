"""Normalize user text: fix typos, strip question noise, extract searchable subjects."""

import re
from difflib import SequenceMatcher

COMMON_TYPOS: dict[str, str] = {
    "hte": "the",
    "teh": "the",
    "thier": "their",
    "adress": "address",
    "addres": "address",
    "adres": "address",
    "adresss": "address",
    "propety": "property",
    "propert": "property",
    "parcle": "parcel",
    "parcell": "parcel",
    "zonning": "zoning",
    "zonig": "zoning",
    "zoing": "zoning",
    "zoningg": "zoning",
    "flod": "flood",
    "fld": "flood",
    "floood": "flood",
    "schol": "school",
    "shcool": "school",
    "precint": "precinct",
    "precinctt": "precinct",
    "ownr": "owner",
    "owneer": "owner",
    "subdivison": "subdivision",
    "subdivisionn": "subdivision",
    "subdivsion": "subdivision",
    "sailsbury": "salisbury",
    "salsibury": "salisbury",
    "clevland": "cleveland",
    "woodlef": "woodleaf",
}

ADDRESS_NOISE_WORDS = {
    "WHAT", "WHATS", "WHO", "WHERE", "WHEN", "WHY", "HOW",
    "IS", "ARE", "WAS", "WERE", "DOES", "DO", "DID", "CAN", "COULD", "WOULD",
    "THE", "A", "AN", "FOR", "AT", "ON", "IN", "OF", "TO", "FROM", "INTO",
    "TELL", "ME", "ABOUT", "SHOW", "FIND", "GET", "PLEASE", "YOU", "I",
    "ZONING", "ZONE", "DISTRICT", "FLOOD", "FEMA", "SCHOOL", "SCHOOLS",
    "SOIL", "SOILS", "VOTING", "PRECINCT", "PARK", "PARKS", "WATERSHED",
    "PROPERTY", "PARCEL", "INFO", "INFORMATION", "DETAILS", "CONTEXT", "REPORT",
    "NC", "NORTH", "CAROLINA", "ROWAN", "COUNTY",
    "FALL", "FALLS", "BELONG", "BELONGS", "LOCATED", "SITUATED", "LIE", "LIES",
    "CATEGORY", "CLASS", "CLASSIFICATION", "DESIGNATION", "TYPE",
}

STREET_SUFFIXES = (
    "ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "LN", "LANE", "AVE", "AVENUE",
    "BLVD", "CT", "COURT", "WAY", "CIR", "CIRCLE", "TRL", "TRAIL", "HWY",
    "HIGHWAY", "PKWY", "PARKWAY", "PL", "PLACE", "MT", "MOUNT",
)

ROWAN_PLACE_NAMES: dict[str, str] = {
    "SALISBURY": "SALISBURY",
    "SALS": "SALISBURY",
    "CHINA GROVE": "CHINA GROVE",
    "CHINAGROVE": "CHINA GROVE",
    "CLEVELAND": "CLEVELAND",
    "LANDIS": "LANDIS",
    "SPENCER": "SPENCER",
    "EAST SPENCER": "EAST SPENCER",
    "FAITH": "FAITH",
    "GRANITE QUARRY": "GRANITE QUARRY",
    "ROCKWELL": "ROCKWELL",
    "WOODLEAF": "WOODLEAF",
    "MOUNT ULLA": "MOUNT ULLA",
    "MT ULLA": "MOUNT ULLA",
    "BARBER": "BARBER",
    "GOLD HILL": "GOLD HILL",
}

PIN_FULL_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2}-\d{4})\b", re.IGNORECASE)
PIN_LABEL_PATTERN = re.compile(
    r"\b(?:pin|parcel(?:\s+(?:id|#|number))?|tax\s+id)\s*[:#]?\s*([A-Za-z0-9\-]+)\b",
    re.IGNORECASE,
)

ADDRESS_FRAGMENT_PATTERN = re.compile(
    r"\b(\d+\s+(?:[NSEW]\s+)?(?:[A-Za-z0-9]+\s+){0,8}"
    r"(?:"
    + "|".join(re.escape(s) for s in STREET_SUFFIXES)
    + r")\b"
    r"(?:\s+[NSEW])?)",
    re.IGNORECASE,
)

LOOSE_ADDRESS_PATTERN = re.compile(
    r"\b(\d+\s+(?:[NSEW]\s+)?(?:[A-Za-z0-9]+\s+){1,10}[A-Za-z0-9]+)\b",
    re.IGNORECASE,
)

STREET_NAME_PATTERN = re.compile(
    r"\b((?:[NSEW]\s+)?(?:[A-Za-z0-9]+\s+){0,6}"
    r"(?:"
    + "|".join(re.escape(s) for s in STREET_SUFFIXES)
    + r"))\b",
    re.IGNORECASE,
)

# Remove trailing question clutter: "fall in", "belong to", etc.
TRAILING_CLAUSE_PATTERN = re.compile(
    r"\s+(?:"
    r"fall(?:s)?\s+(?:in(?:to)?|under)|"
    r"belong(?:s)?\s+to|"
    r"(?:is|are|was|were)\s+(?:in|at|on|located|situated)\b.*|"
    r"in\s+(?:what|which)\s+(?:zoning|zone|district|flood|school).*"
    r")\??$",
    re.IGNORECASE,
)

# Leading question phrases — applied repeatedly until stable.
LEADING_PHRASE_PATTERNS = [
    re.compile(r"^(?:can you|could you|please|help me)\s+", re.I),
    re.compile(r"^(?:tell me|show me|look up|lookup|find|search for|get|list)\s+(?:about\s+)?", re.I),
    re.compile(r"^(?:who owns|who is the owner of)\s+", re.I),
    re.compile(r"^what(?:'s|s|\s+is|\s+are|\s+was|\s+were)\s+(?:the\s+)?", re.I),
    re.compile(r"^where(?:'s|\s+is|\s+are)\s+(?:the\s+)?", re.I),
    re.compile(r"^how do i (?:find|look up|get)\s+(?:the\s+)?", re.I),
    re.compile(
        r"^(?:zoning|flood(?:\s+zone|\s+panel)?|fema(?:\s+flood)?|school(?:s)?|soil(?:s)?|"
        r"voting(?:\s+precinct)?|watershed|property(?:\s+info|\s+details)?|(?:county\s+)?parks?)\s+(?:for|of|at|on|near)\s+",
        re.I,
    ),
    re.compile(
        r"^(?:what\s+)?(?:zoning|zone|district)\s+(?:does|do|is|are|would)\s+",
        re.I,
    ),
    re.compile(
        r"^(?:what\s+)?(?:kind|type)\s+of\s+(?:zoning|zone|district)\s+(?:is|are|does)\s+",
        re.I,
    ),
    re.compile(r"^(?:which|what)\s+(?:zoning|zone|district|flood|school)\s+(?:is|are|does)\s+", re.I),
]

CONTEXT_FOCUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bflood(?:\s+zone|\s+panel|\s+info)?\b|\bfema\b", re.I), "flood"),
    (re.compile(r"\bschool(?:s)?\b|\b(?:elementary|middle|high)\s+school\b", re.I), "schools"),
    (re.compile(r"\bsoil(?:s)?\b", re.I), "soils"),
    (re.compile(r"\bvoting(?:\s+precinct)?\b|\bprecinct\b", re.I), "voting"),
    (re.compile(r"\bwatershed\b", re.I), "watershed"),
    (re.compile(r"\b(?:county\s+)?parks?\b|\bnearby\s+park\b", re.I), "parks"),
    (re.compile(r"\bproperty(?:\s+info|\s+details|\s+context|\s+report)?\b", re.I), "property_info"),
    (
        re.compile(
            r"\b(?:county\s+)?zoning\b|"
            r"\b(?:what|which)\s+(?:\w+\s+){0,8}(?:zoning|zone|district)\b|"
            r"\b(?:zoning|zone|district)\s+(?:for|of|at|on|does|is)\b",
            re.I,
        ),
        "zoning",
    ),
]

CONTEXT_KEYWORDS = {
    "zoning", "zone", "district", "flood", "fema", "school", "schools", "soil", "soils",
    "voting", "precinct", "watershed", "property", "park", "parks",
}


def normalize_query_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    cleaned = re.sub(r"\bwhats\b", "what is", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwhat?s\b", "what is", cleaned, flags=re.IGNORECASE)
    words = cleaned.split(" ")
    fixed = [COMMON_TYPOS.get(word.lower(), word) for word in words]
    return " ".join(fixed)


def detect_context_focus(text: str) -> str:
    normalized = normalize_query_text(text)
    for pattern, focus in CONTEXT_FOCUS_PATTERNS:
        if pattern.search(normalized):
            return focus
    return ""


def _strip_leading_phrases(text: str) -> str:
    cleaned = text.strip("?.!,")
    changed = True
    while changed:
        changed = False
        for pattern in LEADING_PHRASE_PATTERNS:
            updated = pattern.sub("", cleaned).strip("?.!,")
            if updated != cleaned:
                cleaned = updated
                changed = True
    return cleaned


def strip_question_wrapper(text: str) -> str:
    """Remove question phrasing and return the best-guess searchable subject."""
    cleaned = normalize_query_text(text).strip("?.!,")
    cleaned = _strip_leading_phrases(cleaned)
    cleaned = TRAILING_CLAUSE_PATTERN.sub("", cleaned).strip("?.!,")
    return cleaned


def extract_pin_from_text(text: str) -> str | None:
    normalized = normalize_query_text(text)
    full = PIN_FULL_PATTERN.search(normalized)
    if full:
        return full.group(1)
    label = PIN_LABEL_PATTERN.search(normalized)
    if label:
        return label.group(1).strip()
    return None


def _trim_subject_tail(value: str) -> str:
    tokens = value.split()
    while tokens and tokens[-1].upper() in ADDRESS_NOISE_WORDS | {word.upper() for word in CONTEXT_KEYWORDS}:
        tokens.pop()
    return " ".join(tokens)


def extract_address_fragment(text: str) -> str | None:
    """Pull a street address like '550 MT HALL RD' from anywhere in the text."""
    normalized = normalize_query_text(text)
    for pattern in (ADDRESS_FRAGMENT_PATTERN, LOOSE_ADDRESS_PATTERN):
        match = pattern.search(normalized)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip("?.!,")
            candidate = _trim_subject_tail(candidate)
            tokens = candidate.upper().split()
            if len(tokens) >= 2 and any(token in STREET_SUFFIXES for token in tokens):
                return " ".join(tokens)
    return None


def extract_street_name_fragment(text: str) -> str | None:
    """Street without house number, e.g. 'Main Street' or 'Woodleaf Road'."""
    normalized = normalize_query_text(text)
    match = STREET_NAME_PATTERN.search(normalized)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip("?.!,")
    return None


def extract_search_subject(text: str) -> tuple[str, str]:
    """
    Extract the best searchable subject from free-form text.

    Returns (subject, hint) where hint is one of:
    address, pin, street, name, raw
    """
    normalized = normalize_query_text(text)
    if not normalized:
        return "", "raw"

    pin = extract_pin_from_text(normalized)
    if pin:
        return pin, "pin"

    address = extract_address_fragment(normalized)
    if address:
        return address, "address"

    stripped = strip_question_wrapper(normalized)
    address = extract_address_fragment(stripped)
    if address:
        return address, "address"

    if stripped and stripped.lower() not in CONTEXT_KEYWORDS:
        pin = extract_pin_from_text(stripped)
        if pin:
            return pin, "pin"

        if re.search(r"\d+\s+[A-Za-z]", stripped):
            return stripped, "address"

        street = extract_street_name_fragment(stripped)
        if street:
            return street, "street"

        if len(stripped) >= 3:
            return stripped, "name"

    street = extract_street_name_fragment(normalized)
    if street:
        return street, "street"

    return normalized, "raw"


def parse_address_parts(text: str) -> tuple[str | None, list[str]]:
    subject, _ = extract_search_subject(text)
    fragment = extract_address_fragment(subject) or subject

    tokens = re.findall(r"[A-Za-z0-9]+", fragment.upper())
    house_number = None
    street_tokens: list[str] = []

    for token in tokens:
        if token in ADDRESS_NOISE_WORDS:
            continue
        if house_number is None and token.isdigit():
            house_number = token
            continue
        if len(token) >= 2:
            street_tokens.append(token)

    return house_number, street_tokens


def parse_city_from_query(text: str) -> str | None:
    """Extract a Rowan municipality name from a free-form address query."""
    normalized = normalize_query_text(text).upper()
    for place in sorted(ROWAN_PLACE_NAMES, key=len, reverse=True):
        if place in normalized:
            return ROWAN_PLACE_NAMES[place]
    return None


def with_city_from_message(address: str, message: str) -> str:
    """Append a municipality from the full message when it is missing from the address."""
    city = parse_city_from_query(message)
    if not city:
        return address
    upper = address.upper()
    if city in upper or upper.endswith(city[:4]):
        return address
    return f"{address} {city}"


def fuzzy_best_match(query: str, candidates: list[str], *, min_ratio: float = 0.72) -> str | None:
    if not candidates:
        return None
    query_norm = query.upper().strip()
    best_score = 0.0
    best_value = None
    for candidate in candidates:
        score = SequenceMatcher(None, query_norm, candidate.upper().strip()).ratio()
        if score > best_score:
            best_score = score
            best_value = candidate
    if best_score >= min_ratio:
        return best_value
    return None


def fuzzy_score(query: str, candidate: str) -> float:
    return SequenceMatcher(None, query.upper().strip(), candidate.upper().strip()).ratio()

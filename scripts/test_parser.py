"""Parser and normalization checks for free-form phrasing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.query_parser import parse_query


ZONING_PHRASES = [
    "what is hte zoning for 550 mt hall rd",
    "what zoning does 550 mt hall rd fall in",
    "what zone is 550 mt hall rd in",
    "what district is 550 mt hall rd",
    "zoning for 550 mt hall rd",
    "what kind of zoning is 550 mt hall rd in",
    "which zoning is 550 mt hall rd in",
    "550 mt hall rd zoning",
]

OWNER_PHRASES = [
    "Who owns 550 MT HALL RD",
    "who is the owner of 550 mt hall rd",
    "tell me who owns 550 mt hall rd",
]

PIN_CONTEXT_PHRASES = [
    ("what is the zoning for PIN 5733-04-51-7482", "zoning"),
    ("flood zone for parcel id 5733-04-51-7482", "flood"),
]

TYPO_PHRASES = [
    ("what is hte zonning for 550 mt hall rd", "zoning"),
    ("whats the zoing for 550 mt hall rd", "zoning"),
    ("what is the flod zone for 550 mt hall rd", "flood"),
]


def _assert_address_intent(message: str, *, focus: str = ""):
    intent = parse_query(message)
    assert intent is not None, f"no intent: {message!r}"
    assert intent.intent_type == "address", f"{message!r} -> {intent.intent_type}"
    assert "550" in intent.value.upper(), f"{message!r} -> value {intent.value!r}"
    assert "HALL" in intent.value.upper(), f"{message!r} -> value {intent.value!r}"
    if focus:
        assert intent.context_focus == focus, f"{message!r} focus={intent.context_focus}"
    assert "what zoning does" not in intent.value.lower()
    assert "fall in" not in intent.value.lower()


def _assert_pin_intent(message: str, *, focus: str = ""):
    intent = parse_query(message)
    assert intent is not None, f"no intent: {message!r}"
    assert intent.intent_type == "pin", f"{message!r} -> {intent.intent_type}"
    assert "5733" in intent.value, f"{message!r} -> value {intent.value!r}"
    if focus:
        assert intent.context_focus == focus, f"{message!r} focus={intent.context_focus!r}"


def run_tests():
    for phrase in ZONING_PHRASES:
        _assert_address_intent(phrase, focus="zoning")

    for phrase in OWNER_PHRASES:
        intent = parse_query(phrase)
        assert intent is not None
        assert intent.intent_type == "address"
        assert "550" in intent.value.upper()

    for phrase, focus in PIN_CONTEXT_PHRASES:
        _assert_pin_intent(phrase, focus=focus)

    for phrase, focus in TYPO_PHRASES:
        _assert_address_intent(phrase, focus=focus)

    intent = parse_query("what is the flood zone for 550 mt hall rd")
    assert intent.context_focus == "flood"

    intent = parse_query("PIN 5733-04-51-7482")
    assert intent.intent_type == "pin"

    intent = parse_query("what parks are near 550 mt hall rd")
    assert intent.context_focus == "parks"

    intent = parse_query("does parcel 304 157 have flood ?")
    assert intent is not None
    assert intent.intent_type == "pin"
    assert intent.field == "PARCEL_ID"
    assert intent.value == "304 157"
    assert intent.context_focus == "flood"

    print("OK (parser normalization)")


if __name__ == "__main__":
    run_tests()

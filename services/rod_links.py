"""Register of Deeds (ROD) deep links from parcel deed book/page fields."""

from urllib.parse import urlencode

ROD_DEED_SEARCH_BASE = (
    "https://rod.rowancountync.gov/external/LandRecords/protected/SrchBookPage.aspx"
)


def _clean_deed_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "0"}:
        return None
    return text


def build_rod_book_page_url(book: object, page: object) -> str | None:
    """Build ROD book/page search URL (deed or plat)."""
    book_value = _clean_deed_value(book)
    page_value = _clean_deed_value(page)
    if not book_value or not page_value:
        return None

    params = urlencode({"bAutoSearch": "true", "bk": book_value, "pg": page_value})
    return f"{ROD_DEED_SEARCH_BASE}?{params}"


def build_rod_deed_search_url(deed_book: object, deed_page: object) -> str | None:
    """Build ROD deed book/page search URL from parcel DEEDBOOK and DEEDPAGE values."""
    return build_rod_book_page_url(deed_book, deed_page)


def format_book_page_line(label: str, book: object, page: object) -> str | None:
    """Human-readable book/page line with ROD search URL."""
    book_value = _clean_deed_value(book)
    page_value = _clean_deed_value(page)
    url = build_rod_book_page_url(book_value, page_value)
    if not book_value or not page_value or not url:
        return None
    return f"{label}: Book {book_value}, Page {page_value}\n{url}"


def format_deed_record_line(deed_book: object, deed_page: object) -> str:
    """Human-readable deed book/page line, or a placeholder when missing."""
    return format_book_page_line("Deed record", deed_book, deed_page) or (
        "Deed record: No recorded deed book/page for this parcel."
    )


def format_plat_record_line(plat_book: object, plat_page: object) -> str:
    """Human-readable plat book/page line, or a placeholder when missing."""
    return format_book_page_line("Plat record", plat_book, plat_page) or (
        "Plat record: No recorded plat for this parcel."
    )

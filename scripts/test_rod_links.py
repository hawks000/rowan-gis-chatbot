"""Quick tests for Register of Deeds deed URL builder."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.rod_links import build_rod_deed_search_url, format_deed_record_line, format_plat_record_line


def test_build_url():
    url = build_rod_deed_search_url("1260", "87")
    assert url == (
        "https://rod.rowancountync.gov/external/LandRecords/protected/SrchBookPage.aspx"
        "?bAutoSearch=true&bk=1260&pg=87"
    )


def test_missing_values():
    assert build_rod_deed_search_url(None, "87") is None
    assert build_rod_deed_search_url("1260", "") is None
    assert build_rod_deed_search_url("0", "87") is None


def test_format_plat_line():
    line = format_plat_record_line("1260", "87")
    assert "Plat record: Book 1260, Page 87" in line
    assert "bk=1260" in line
    assert "pg=87" in line


def test_missing_deed_placeholder():
    line = format_deed_record_line(None, "87")
    assert "No recorded deed" in line


def test_missing_plat_placeholder():
    line = format_plat_record_line(None, None)
    assert "No recorded plat" in line


def test_format_deed_line():
    line = format_deed_record_line("1138", "317")
    assert "Book 1138, Page 317" in line
    assert "bk=1138" in line
    assert "pg=317" in line


if __name__ == "__main__":
    test_build_url()
    test_missing_values()
    test_missing_deed_placeholder()
    test_missing_plat_placeholder()
    test_format_deed_line()
    test_format_plat_line()
    print("rod_links tests passed")

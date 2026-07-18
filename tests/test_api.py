"""Tests for the CSV parsing in the ista API client."""
from datetime import date

from custom_components.ista_online.api import (
    Reading,
    _parse_danish_float,
    _parse_ddmmyyyy,
    parse_csv,
)


SAMPLE_CSV = (
    '"Måler","Dato","Korrigeret Forbrug","Rum"\r\n'
    '"735493320","14-05-2019","12,5","Børneværelse"\r\n'
    '"735493283","14-05-2019","0","Soveværelse"\r\n'
    '"735493306","15-05-2019","3,75","Bryggers"\r\n'
    '"","15-05-2019","9,9","Ukendt"\r\n'  # missing meter id -> skipped
).encode("utf-8-sig")


def test_parse_danish_float():
    assert _parse_danish_float("1234,5") == 1234.5
    assert _parse_danish_float("1.234,56") == 1234.56
    assert _parse_danish_float("") is None
    assert _parse_danish_float("abc") is None


def test_parse_ddmmyyyy():
    assert _parse_ddmmyyyy("14-05-2019") == date(2019, 5, 14)
    assert _parse_ddmmyyyy("bad") is None


def test_parse_csv():
    readings = parse_csv(SAMPLE_CSV)
    assert len(readings) == 3  # row with empty meter id dropped
    by_id = {r.meter_id: r for r in readings}
    assert by_id["735493320"].value == 12.5
    assert by_id["735493320"].room == "Børneværelse"
    assert by_id["735493320"].day == date(2019, 5, 14)


def test_reading_key_unique():
    r = Reading("111", "Stue", date(2025, 5, 1), 3.0)
    assert r.key == "111|2025-05-01"


def test_parse_csv_empty():
    assert parse_csv(b"") == []

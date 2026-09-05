"""Tests for meter name resolution."""
from datetime import date

from custom_components.ista_online.api import Reading
from custom_components.ista_online.names import resolve_meter_names

DAY = date(2025, 5, 1)


def test_room_is_used_when_unique():
    readings = [Reading("111", "Stue", DAY, 1.0), Reading("222", "Bad", DAY, 1.0)]
    assert resolve_meter_names(readings) == {"111": "Stue", "222": "Bad"}


def test_duplicate_rooms_get_the_meter_id_appended():
    readings = [
        Reading("111", "Værelse", DAY, 1.0),
        Reading("222", "Værelse", DAY, 1.0),
        Reading("333", "Stue", DAY, 1.0),
    ]
    assert resolve_meter_names(readings) == {
        "111": "Værelse (111)",
        "222": "Værelse (222)",
        "333": "Stue",
    }


def test_placeholder_rooms_fall_back_to_the_meter_id():
    readings = [Reading("111", "?", DAY, 1.0), Reading("222", "", DAY, 1.0)]
    assert resolve_meter_names(readings) == {"111": "111", "222": "222"}


def test_alias_wins_over_room():
    readings = [Reading("111", "Værelse", DAY, 1.0), Reading("222", "Værelse", DAY, 1.0)]
    names = resolve_meter_names(readings, {"111": "Børneværelse"})
    assert names == {"111": "Børneværelse", "222": "Værelse"}


def test_blank_alias_is_ignored():
    readings = [Reading("111", "Stue", DAY, 1.0)]
    assert resolve_meter_names(readings, {"111": "   "}) == {"111": "Stue"}


def test_duplicate_aliases_are_still_disambiguated():
    readings = [Reading("111", "Stue", DAY, 1.0), Reading("222", "Bad", DAY, 1.0)]
    names = resolve_meter_names(readings, {"111": "Kælder", "222": "Kælder"})
    assert names == {"111": "Kælder (111)", "222": "Kælder (222)"}


def test_alias_for_a_meter_without_readings_is_kept():
    names = resolve_meter_names([], {"999": "Loft"})
    assert names == {"999": "Loft"}

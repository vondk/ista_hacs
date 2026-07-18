"""Tests for the heating-year price logic."""
from datetime import date

from custom_components.ista_online import prices


REFERENCE = [
    prices.make_period(2020, 0.5849853),
    prices.make_period(2021, 0.6528265),
    prices.make_period(2022, 0.7720811),
    prices.make_period(2023, 1.131273854),
]


def test_heating_year_bounds():
    assert prices.heating_year_bounds(2025) == (date(2025, 5, 1), date(2026, 4, 30))


def test_heating_year_for_date():
    assert prices.heating_year_for_date(date(2025, 5, 1)) == 2025
    assert prices.heating_year_for_date(date(2025, 4, 30)) == 2024
    assert prices.heating_year_for_date(date(2025, 12, 31)) == 2025


def test_find_price_inside_period():
    assert prices.find_price(REFERENCE, date(2021, 12, 1)) == 0.6528265
    # April belongs to the previous May's heating year
    assert prices.find_price(REFERENCE, date(2022, 4, 15)) == 0.6528265


def test_find_price_fallbacks():
    assert prices.find_price(REFERENCE, date(2018, 1, 1)) == 0.5849853  # before all
    assert prices.find_price(REFERENCE, date(2030, 1, 1)) == 1.131273854  # after all
    assert prices.find_price([], date(2025, 1, 1)) is None


def test_upsert_period_replaces():
    updated = prices.upsert_period(REFERENCE, 2023, 2.5)
    assert len(updated) == len(REFERENCE)
    assert prices.find_price(updated, date(2023, 6, 1)) == 2.5


def test_upsert_period_adds():
    updated = prices.upsert_period(REFERENCE, 2024, 1.5)
    assert len(updated) == len(REFERENCE) + 1
    assert prices.find_price(updated, date(2024, 6, 1)) == 1.5

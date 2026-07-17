"""Heating-year price handling.

The unit price (DKK per HCA unit) is fixed for a heating year that runs from
1 May to 30 April, but changes between years. Prices are stored in the config
entry options as a list of periods so historical cost can be computed correctly.
"""
from __future__ import annotations

from datetime import date

from .const import CONF_PERIOD_END, CONF_PERIOD_START, CONF_PRICE


def heating_year_bounds(start_year: int) -> tuple[date, date]:
    """Return (start, end) dates for the heating year beginning in ``start_year``."""
    return date(start_year, 5, 1), date(start_year + 1, 4, 30)


def heating_year_for_date(day: date) -> int:
    """Return the start year of the heating year that ``day`` falls in.

    Months from May onward belong to the heating year labelled with that year;
    January–April belong to the heating year that started the previous May.
    """
    return day.year if day.month >= 5 else day.year - 1


def make_period(start_year: int, price: float) -> dict:
    """Build a stored price-period dict for a heating year."""
    start, end = heating_year_bounds(start_year)
    return {
        CONF_PERIOD_START: start.isoformat(),
        CONF_PERIOD_END: end.isoformat(),
        CONF_PRICE: float(price),
    }


def _period_dates(period: dict) -> tuple[date, date]:
    return (
        date.fromisoformat(period[CONF_PERIOD_START]),
        date.fromisoformat(period[CONF_PERIOD_END]),
    )


def sorted_periods(prices: list[dict]) -> list[dict]:
    """Return price periods sorted by start date."""
    return sorted(prices, key=lambda p: p[CONF_PERIOD_START])


def find_price(prices: list[dict], day: date) -> float | None:
    """Return the price valid for ``day``.

    If the day falls inside a defined period, that price is used. Otherwise the
    nearest edge price is used (earliest price for dates before all periods, the
    latest price for dates after) — mirroring the reference dashboard's fallback.
    """
    if not prices:
        return None

    ordered = sorted_periods(prices)
    for period in ordered:
        start, end = _period_dates(period)
        if start <= day <= end:
            return float(period[CONF_PRICE])

    first_start, _ = _period_dates(ordered[0])
    if day < first_start:
        return float(ordered[0][CONF_PRICE])
    return float(ordered[-1][CONF_PRICE])


def upsert_period(prices: list[dict], start_year: int, price: float) -> list[dict]:
    """Add or replace the price period for ``start_year`` and return a new list."""
    start_iso = heating_year_bounds(start_year)[0].isoformat()
    kept = [p for p in prices if p.get(CONF_PERIOD_START) != start_iso]
    kept.append(make_period(start_year, price))
    return sorted_periods(kept)

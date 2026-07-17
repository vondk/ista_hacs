"""Build and push external long-term statistics for ista Online data.

ista provides daily corrected-consumption readings with a delay, so we inject
them straight into Home Assistant's statistics on their real dates rather than
recording forward from install time. The full history is rebuilt and pushed on
every refresh; ``async_add_external_statistics`` overwrites from the earliest
start, so repeated pushes are idempotent.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from homeassistant.components.recorder.models import StatisticData, StatisticMetadata
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import Reading
from .const import (
    DOMAIN,
    STAT_COST_UNIT,
    STAT_ENERGY_UNIT,
    STAT_TOTAL_COST,
    STAT_TOTAL_ENERGY,
)
from .prices import find_price

_LOGGER = logging.getLogger(__name__)


def _day_start(day: date) -> datetime:
    """Return the tz-aware, hour-aligned local midnight for a day."""
    return dt_util.start_of_local_day(day)


def _metadata(statistic_id: str, name: str, unit: str) -> StatisticMetadata:
    return StatisticMetadata(
        has_mean=False,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )


def _cumulative_series(daily: dict[date, float]) -> list[StatisticData]:
    """Turn a {day: value} map into cumulative-sum statistic points."""
    running = 0.0
    points: list[StatisticData] = []
    for day in sorted(daily):
        running += daily[day]
        points.append(
            StatisticData(start=_day_start(day), state=running, sum=running)
        )
    return points


def _meter_statistic_id(meter_id: str) -> str:
    return f"{DOMAIN}:meter_{meter_id}_energy"


def async_push_statistics(
    hass: HomeAssistant,
    readings: list[Reading],
    prices: list[dict],
) -> None:
    """Rebuild and push all statistics from the accumulated readings."""
    if not readings:
        _LOGGER.debug("No readings to push to statistics")
        return

    # Per-meter consumption + friendly names (last-seen room wins).
    per_meter: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    meter_names: dict[str, str] = {}
    total_energy: dict[date, float] = defaultdict(float)
    total_cost: dict[date, float] = defaultdict(float)

    for reading in readings:
        per_meter[reading.meter_id][reading.day] += reading.value
        meter_names[reading.meter_id] = reading.room
        total_energy[reading.day] += reading.value
        price = find_price(prices, reading.day)
        if price is not None:
            total_cost[reading.day] += reading.value * price

    # Total consumption (surfaced as gas via the m³ unit for the Energy dashboard).
    async_add_external_statistics(
        hass,
        _metadata(STAT_TOTAL_ENERGY, "ista Online samlet forbrug", STAT_ENERGY_UNIT),
        _cumulative_series(total_energy),
    )

    # Total cost in DKK using the correct per-heating-year price.
    if any(total_cost.values()):
        async_add_external_statistics(
            hass,
            _metadata(STAT_TOTAL_COST, "ista Online omkostning", STAT_COST_UNIT),
            _cumulative_series(total_cost),
        )

    # One consumption statistic per meter, named after its room.
    for meter_id, daily in per_meter.items():
        name = f"ista Online {meter_names.get(meter_id, meter_id)}"
        async_add_external_statistics(
            hass,
            _metadata(_meter_statistic_id(meter_id), name, STAT_ENERGY_UNIT),
            _cumulative_series(daily),
        )

    _LOGGER.debug(
        "Pushed statistics: total + cost + %d meters (%d readings)",
        len(per_meter),
        len(readings),
    )

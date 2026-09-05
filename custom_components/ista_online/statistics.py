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

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics

try:
    # mean_type replaced the deprecated has_mean flag in newer Home Assistant.
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_TYPE_NONE: object | None = StatisticMeanType.NONE
except ImportError:  # pragma: no cover - older HA without StatisticMeanType
    _MEAN_TYPE_NONE = None
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .api import Reading
from .const import (
    DOMAIN,
    STAT_COST_UNIT,
    STAT_ENERGY_UNIT,
    STAT_TOTAL_COST,
    STAT_TOTAL_ENERGY,
)
from .names import resolve_meter_names
from .prices import find_price

_LOGGER = logging.getLogger(__name__)

# HA's Energy dashboard only offers a statistic as a gas source when its
# metadata declares the "volume" unit class -- without this, m³ statistics
# are invisible to the "select a statistic" pickers even though the unit
# itself matches.
_UNIT_CLASSES: dict[str, str] = {STAT_ENERGY_UNIT: VolumeConverter.UNIT_CLASS}


def _day_start(day: date) -> datetime:
    """Return the tz-aware, hour-aligned local midnight for a day."""
    return dt_util.start_of_local_day(day)


def _metadata(statistic_id: str, name: str, unit: str) -> StatisticMetaData:
    meta: dict = {
        "has_sum": True,
        "name": name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    if _MEAN_TYPE_NONE is not None:
        meta["mean_type"] = _MEAN_TYPE_NONE
        meta["unit_class"] = _UNIT_CLASSES.get(unit)
    else:
        meta["has_mean"] = False
    return meta  # type: ignore[return-value]


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


def meter_energy_statistic_id(meter_id: str) -> str:
    """External statistic id for one meter's consumption."""
    return f"{DOMAIN}:meter_{meter_id}_energy"


def meter_cost_statistic_id(meter_id: str) -> str:
    """External statistic id for one meter's cost."""
    return f"{DOMAIN}:meter_{meter_id}_cost"


def async_push_statistics(
    hass: HomeAssistant,
    readings: list[Reading],
    prices: list[dict],
    names: dict[str, str] | None = None,
) -> None:
    """Rebuild and push all statistics from the accumulated readings."""
    if not readings:
        _LOGGER.debug("No readings to push to statistics")
        return

    meter_names = names or resolve_meter_names(readings)

    per_meter: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    per_meter_cost: dict[str, dict[date, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    total_energy: dict[date, float] = defaultdict(float)
    total_cost: dict[date, float] = defaultdict(float)

    for reading in readings:
        per_meter[reading.meter_id][reading.day] += reading.value
        total_energy[reading.day] += reading.value
        # The unit price is the same for every meter, so the per-meter costs
        # add up to exactly the total cost.
        price = find_price(prices, reading.day)
        if price is not None:
            cost = reading.value * price
            per_meter_cost[reading.meter_id][reading.day] += cost
            total_cost[reading.day] += cost

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

    # Consumption and cost per meter, named after its room / user alias.
    for meter_id, daily in per_meter.items():
        name = f"ista Online {meter_names.get(meter_id, meter_id)}"
        async_add_external_statistics(
            hass,
            _metadata(meter_energy_statistic_id(meter_id), name, STAT_ENERGY_UNIT),
            _cumulative_series(daily),
        )

        daily_cost = per_meter_cost.get(meter_id)
        if daily_cost and any(daily_cost.values()):
            async_add_external_statistics(
                hass,
                _metadata(
                    meter_cost_statistic_id(meter_id),
                    f"{name} omkostning",
                    STAT_COST_UNIT,
                ),
                _cumulative_series(daily_cost),
            )

    _LOGGER.debug(
        "Pushed statistics: total + cost + %d meters (%d readings)",
        len(per_meter),
        len(readings),
    )

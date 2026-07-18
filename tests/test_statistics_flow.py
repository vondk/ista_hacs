"""End-to-end test: set up the entry and verify statistics land in the recorder."""
from datetime import date
from unittest.mock import AsyncMock, patch

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ista_online.api import Reading
from custom_components.ista_online.const import (
    CONF_CONS_ID,
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICES,
    CONF_USERNAME,
    DOMAIN,
    STAT_TOTAL_COST,
    STAT_TOTAL_ENERGY,
)
from custom_components.ista_online.prices import make_period

# Two days, two meters. Totals: energy = 10+5+4 = 19; meter 111 = 15; meter 222 = 4.
# Price for heating year 2025 is 2.0 DKK/unit -> cost = 19 * 2.0 = 38.0.
SAMPLE_READINGS = [
    Reading("111", "Stue", date(2025, 5, 1), 10.0),
    Reading("111", "Stue", date(2025, 5, 2), 5.0),
    Reading("222", "Bad", date(2025, 5, 1), 4.0),
]


async def _wait_recording_done(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    await get_instance(hass).async_block_till_done()
    await hass.async_block_till_done()


async def _sum_by_id(hass: HomeAssistant, statistic_ids: set[str]) -> dict[str, float]:
    start = dt_util.start_of_local_day(date(2025, 4, 30))
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        None,
        statistic_ids,
        "day",
        None,
        {"sum"},
    )
    return {sid: rows[-1]["sum"] for sid, rows in stats.items() if rows}


async def test_setup_pushes_external_statistics(recorder_mock, enable_custom_integrations, hass: HomeAssistant):
    """Setting up the entry injects total, cost and per-meter statistics."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user",
            CONF_PASSWORD: "secret",
            CONF_CONS_ID: "1",
            CONF_METER_TYPE: "HCA",
        },
        options={CONF_PRICES: [make_period(2025, 2.0)]},
        unique_id="1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.ista_online.IstaApiClient.async_fetch",
        new=AsyncMock(return_value=SAMPLE_READINGS),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await _wait_recording_done(hass)

    ids = {STAT_TOTAL_ENERGY, STAT_TOTAL_COST, f"{DOMAIN}:meter_111_energy", f"{DOMAIN}:meter_222_energy"}
    sums = await _sum_by_id(hass, ids)

    assert sums[STAT_TOTAL_ENERGY] == 19.0
    assert sums[STAT_TOTAL_COST] == 38.0
    assert sums[f"{DOMAIN}:meter_111_energy"] == 15.0
    assert sums[f"{DOMAIN}:meter_222_energy"] == 4.0


async def test_sensors_created(recorder_mock, enable_custom_integrations, hass: HomeAssistant):
    """The diagnostic sensors reflect the fetched data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user",
            CONF_PASSWORD: "secret",
            CONF_CONS_ID: "1",
            CONF_METER_TYPE: "HCA",
        },
        options={CONF_PRICES: [make_period(2025, 2.0)]},
        unique_id="1",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.ista_online.IstaApiClient.async_fetch",
        new=AsyncMock(return_value=SAMPLE_READINGS),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    total_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_total_consumption"
    )
    assert total_id is not None
    total = hass.states.get(total_id)
    assert float(total.state) == 19.0
    assert total.attributes["meter_count"] == 2

    latest_id = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_latest_reading"
    )
    assert latest_id is not None
    assert hass.states.get(latest_id).state.startswith("2025-05-02")

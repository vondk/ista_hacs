"""Tests for the ista Online config flow."""
from datetime import date
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ista_online.api import (
    DiscoveredMeter,
    ExportPeriod,
    IstaAuthError,
    IstaConnectionError,
    Reading,
)
from custom_components.ista_online.const import (
    CONF_CONS_ID,
    CONF_FROM_PERIOD,
    CONF_METER_NAMES,
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICE,
    CONF_PRICES,
    CONF_USERNAME,
    DOMAIN,
)

CREDENTIALS_INPUT = {
    CONF_USERNAME: "user",
    CONF_PASSWORD: "secret",
}

ONE_DISCOVERED_METER = [DiscoveredMeter(meter_type="HCA", cons_id="13958518442")]

# The real dropdown values: one heating year per entry, newest first.
PERIODS = [
    ExportPeriod(
        value="01.05.2025 - 30.04.2026", label="01.05.2025 - 30.04.2026"
    ),
    ExportPeriod(
        value="01.05.2024 - 30.04.2025", label="01.05.2024 - 30.04.2025"
    ),
]
OLDEST = PERIODS[-1].value
NEWEST = PERIODS[0].value

# Two heating years: 2024 (May 2024 – Apr 2025) and 2025 (from May 2025).
READINGS = [
    Reading("111", "Stue", date(2024, 6, 1), 3.0),
    Reading("111", "Stue", date(2025, 6, 1), 4.0),
    Reading("222", "Bad", date(2025, 6, 1), 5.0),
]

ENTRY_DATA = {
    CONF_USERNAME: "user",
    CONF_PASSWORD: "secret",
    CONF_CONS_ID: "1",
    CONF_METER_TYPE: "HCA",
}


def _patch_client(
    *,
    meters=ONE_DISCOVERED_METER,
    periods=PERIODS,
    readings=READINGS,
    fetch_side_effect=None,
):
    """Patch the three client calls the config flow makes."""
    return (
        patch(
            "custom_components.ista_online.config_flow.IstaApiClient.async_discover_meters",
            new=AsyncMock(return_value=meters),
        ),
        patch(
            "custom_components.ista_online.config_flow.IstaApiClient.async_discover_periods",
            new=AsyncMock(return_value=periods),
        ),
        patch(
            "custom_components.ista_online.config_flow.IstaApiClient.async_fetch",
            new=AsyncMock(return_value=readings, side_effect=fetch_side_effect),
        ),
        patch("custom_components.ista_online.async_setup_entry", return_value=True),
    )


async def test_user_flow_success(enable_custom_integrations, hass: HomeAssistant):
    """A valid login discovers the meter and periods, then asks a price per year."""
    a, b, c, d = _patch_client()
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )
        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "history"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {CONF_FROM_PERIOD: OLDEST}
        )
        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "prices"
        # One field per imported heating year, newest first.
        assert list(result3["data_schema"].schema) == ["2025/2026", "2024/2025"]

        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"], {"2025/2026": 1.5, "2024/2025": 1.13}
        )
        await hass.async_block_till_done()

    assert result4["type"] == FlowResultType.CREATE_ENTRY
    assert result4["data"][CONF_CONS_ID] == "13958518442"
    assert result4["data"][CONF_METER_TYPE] == "HCA"
    assert result4["data"][CONF_FROM_PERIOD] == OLDEST
    assert CONF_PASSWORD in result4["data"]

    prices = sorted(result4["options"][CONF_PRICES], key=lambda p: p["start"])
    assert [p["start"] for p in prices] == ["2024-05-01", "2025-05-01"]
    assert [p[CONF_PRICE] for p in prices] == [1.13, 1.5]


async def test_history_step_defaults_to_oldest_period(
    enable_custom_integrations, hass: HomeAssistant
):
    """The period selector defaults to the oldest entry, i.e. import everything."""
    a, b, c, d = _patch_client()
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )

    key = next(k for k in result2["data_schema"].schema if k == CONF_FROM_PERIOD)
    assert key.default() == OLDEST


async def test_history_step_skipped_without_periods(
    enable_custom_integrations, hass: HomeAssistant
):
    """An unreadable period list falls straight through to the price step."""
    a, b, c, d = _patch_client(periods=[])
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )
        assert result2["step_id"] == "prices"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {"2025/2026": 1.5, "2024/2025": 1.13}
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    # No period was chosen, so the export keeps using ista's own default.
    assert result3["data"][CONF_FROM_PERIOD] == ""


async def test_history_step_import_failure_shows_error(
    enable_custom_integrations, hass: HomeAssistant
):
    """A failing import is reported on the history form so it can be retried."""
    a, b, c, d = _patch_client(fetch_side_effect=IstaConnectionError("down"))
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {CONF_FROM_PERIOD: OLDEST}
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "history"
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_user_flow_multiple_meters(enable_custom_integrations, hass: HomeAssistant):
    """Several discovered meters are offered as a choice before the history step."""
    discovered = [
        DiscoveredMeter(meter_type="HCA", cons_id="111"),
        DiscoveredMeter(meter_type="HCA", cons_id="222"),
    ]
    a, b, c, d = _patch_client(meters=discovered)
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )
        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "meter"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {CONF_CONS_ID: "222"}
        )
        assert result3["step_id"] == "history"

        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"], {CONF_FROM_PERIOD: NEWEST}
        )
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"], {"2025/2026": 1.0, "2024/2025": 1.0}
        )
        await hass.async_block_till_done()

    assert result5["type"] == FlowResultType.CREATE_ENTRY
    assert result5["data"][CONF_CONS_ID] == "222"


async def test_user_flow_no_meters_falls_back_to_manual(
    enable_custom_integrations, hass: HomeAssistant
):
    """Nothing auto-discovered falls back to manual cons_id entry."""
    a, b, c, d = _patch_client(meters=[])
    with a, b, c, d:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )
        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "manual"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {CONF_CONS_ID: "333", CONF_METER_TYPE: "HCA"},
        )
        assert result3["step_id"] == "history"

        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"], {CONF_FROM_PERIOD: OLDEST}
        )
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"], {"2025/2026": 1.0, "2024/2025": 1.0}
        )
        await hass.async_block_till_done()

    assert result5["type"] == FlowResultType.CREATE_ENTRY
    assert result5["data"][CONF_CONS_ID] == "333"


async def test_user_flow_invalid_auth(enable_custom_integrations, hass: HomeAssistant):
    """Bad credentials surface an invalid_auth error during discovery."""
    with patch(
        "custom_components.ista_online.config_flow.IstaApiClient.async_discover_meters",
        new=AsyncMock(side_effect=IstaAuthError("bad")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENTIALS_INPUT
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "user"
    assert result2["errors"] == {"base": "invalid_auth"}


def _mock_entry(hass: HomeAssistant, **kwargs):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.ista_online.prices import make_period

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={CONF_PRICES: [make_period(2025, 1.13)]},
        unique_id="1",
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_upsert_price(enable_custom_integrations, hass: HomeAssistant):
    """The options flow adds a price period for another heating year."""
    entry = _mock_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "prices"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "prices"

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"], {"start_year": 2024, CONF_PRICE: 1.13}
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert len(result3["data"][CONF_PRICES]) == 2


async def test_options_flow_meter_names(
    recorder_mock, enable_custom_integrations, hass: HomeAssistant
):
    """Meter names can be edited and are stored as aliases."""
    entry = _mock_entry(hass)

    with patch(
        "custom_components.ista_online.IstaApiClient.async_fetch",
        new=AsyncMock(return_value=READINGS),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "meter_names"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert set(result2["data_schema"].schema) == {"111", "222"}

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"], {"111": "Soveværelse", "222": "Bryggers"}
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_METER_NAMES] == {
        "111": "Soveværelse",
        "222": "Bryggers",
    }
    # Existing options survive the update.
    assert result3["data"][CONF_PRICES]


async def test_options_flow_history_updates_entry_data(
    enable_custom_integrations, hass: HomeAssistant
):
    """Re-importing stores the new start period on the entry, not in options."""
    entry = _mock_entry(hass)

    with patch(
        "custom_components.ista_online.config_flow.IstaApiClient.async_discover_periods",
        new=AsyncMock(return_value=PERIODS),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "history"}
        )
        assert result2["type"] == FlowResultType.FORM

        result3 = await hass.config_entries.options.async_configure(
            result2["flow_id"], {CONF_FROM_PERIOD: OLDEST}
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_FROM_PERIOD] == OLDEST

"""Tests for the ista Online config flow."""
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ista_online.api import IstaAuthError
from custom_components.ista_online.const import (
    CONF_CONS_ID,
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICE,
    CONF_PRICES,
    CONF_USERNAME,
    DOMAIN,
)

USER_INPUT = {
    CONF_USERNAME: "user",
    CONF_PASSWORD: "secret",
    CONF_CONS_ID: "13958518442",
    CONF_METER_TYPE: "HCA",
    CONF_PRICE: 1.131273854,
}


async def test_user_flow_success(enable_custom_integrations, hass: HomeAssistant):
    """A valid login creates an entry with an initial price period."""
    with patch(
        "custom_components.ista_online.config_flow.IstaApiClient.async_validate",
        new=AsyncMock(return_value=True),
    ), patch(
        "custom_components.ista_online.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_CONS_ID] == "13958518442"
    assert CONF_PASSWORD in result2["data"]
    prices = result2["options"][CONF_PRICES]
    assert len(prices) == 1
    assert prices[0][CONF_PRICE] == 1.131273854


async def test_user_flow_invalid_auth(enable_custom_integrations, hass: HomeAssistant):
    """Bad credentials surface an invalid_auth error."""
    with patch(
        "custom_components.ista_online.config_flow.IstaApiClient.async_validate",
        new=AsyncMock(side_effect=IstaAuthError("bad")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_options_flow_upsert_price(enable_custom_integrations, hass: HomeAssistant):
    """The options flow adds a price period for another heating year."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.ista_online.prices import make_period

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "user",
            CONF_PASSWORD: "secret",
            CONF_CONS_ID: "1",
            CONF_METER_TYPE: "HCA",
        },
        options={CONF_PRICES: [make_period(2025, 1.13)]},
        unique_id="1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"start_year": 2024, CONF_PRICE: 1.13}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert len(result2["data"][CONF_PRICES]) == 2

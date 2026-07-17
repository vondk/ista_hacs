"""Config flow for the ista Online (DK) integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import IstaApiClient, IstaAuthError, IstaConnectionError, IstaError
from .const import (
    CONF_CONS_ID,
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICE,
    CONF_PRICES,
    CONF_USERNAME,
    DEFAULT_METER_TYPE,
    DOMAIN,
)
from .prices import find_price, heating_year_for_date, make_period, upsert_period

_LOGGER = logging.getLogger(__name__)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate credentials by performing a full login + export."""
    client = IstaApiClient(
        hass,
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        cons_id=data[CONF_CONS_ID],
        meter_type=data.get(CONF_METER_TYPE, DEFAULT_METER_TYPE),
    )
    await client.async_validate()


class IstaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ista Online."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CONS_ID])
            self._abort_if_unique_id_configured()
            try:
                await _validate(self.hass, user_input)
            except IstaAuthError:
                errors["base"] = "invalid_auth"
            except IstaConnectionError:
                errors["base"] = "cannot_connect"
            except IstaError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during ista config flow")
                errors["base"] = "unknown"
            else:
                start_year = heating_year_for_date(dt_util.now().date())
                price = float(user_input[CONF_PRICE])
                return self.async_create_entry(
                    title=f"ista Online ({user_input[CONF_CONS_ID]})",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CONS_ID: user_input[CONF_CONS_ID],
                        CONF_METER_TYPE: user_input.get(
                            CONF_METER_TYPE, DEFAULT_METER_TYPE
                        ),
                    },
                    options={CONF_PRICES: [make_period(start_year, price)]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_CONS_ID): str,
                    vol.Required(
                        CONF_METER_TYPE, default=DEFAULT_METER_TYPE
                    ): str,
                    vol.Required(CONF_PRICE): vol.Coerce(float),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            new_data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _validate(self.hass, new_data)
            except IstaAuthError:
                errors["base"] = "invalid_auth"
            except IstaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during ista reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=new_data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={CONF_USERNAME: entry.data[CONF_USERNAME]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> IstaOptionsFlow:
        """Get the options flow for this handler."""
        return IstaOptionsFlow()


class IstaOptionsFlow(OptionsFlow):
    """Manage per-heating-year unit prices."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or update the price for a heating year."""
        prices: list[dict] = list(self.config_entry.options.get(CONF_PRICES, []))

        if user_input is not None:
            start_year = int(user_input["start_year"])
            price = float(user_input[CONF_PRICE])
            new_prices = upsert_period(prices, start_year, price)
            return self.async_create_entry(
                title="", data={CONF_PRICES: new_prices}
            )

        current_year = heating_year_for_date(dt_util.now().date())
        default_price = find_price(prices, dt_util.now().date()) or 0.0

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("start_year", default=current_year): vol.Coerce(int),
                    vol.Required(CONF_PRICE, default=default_price): vol.Coerce(float),
                }
            ),
            description_placeholders={"prices": _format_prices(prices)},
        )


def _format_prices(prices: list[dict]) -> str:
    """Human-readable summary of the stored price periods."""
    if not prices:
        return "(ingen priser endnu)"
    lines = []
    for period in sorted(prices, key=lambda p: p["start"]):
        lines.append(f"{period['start']} → {period['end']}: {period[CONF_PRICE]} DKK")
    return "\n".join(lines)

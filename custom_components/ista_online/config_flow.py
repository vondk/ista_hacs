"""Config flow for the ista Online (DK) integration."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import (
    DiscoveredMeter,
    ExportPeriod,
    IstaApiClient,
    IstaAuthError,
    IstaConnectionError,
    IstaError,
    Reading,
    parse_period_year,
)
from .const import (
    CONF_CONS_ID,
    CONF_FROM_PERIOD,
    CONF_METER_NAMES,
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

_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _client_from(hass: HomeAssistant, data: dict[str, Any], **kwargs) -> IstaApiClient:
    """Build an API client from config-entry style data."""
    return IstaApiClient(
        hass,
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        cons_id=data.get(CONF_CONS_ID, ""),
        meter_type=data.get(CONF_METER_TYPE, DEFAULT_METER_TYPE),
        **kwargs,
    )


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate credentials by performing a full login + export."""
    await _client_from(hass, data).async_validate()


def _period_year(period: ExportPeriod) -> int | None:
    """Best-effort start year for a dropdown entry, used only for ordering."""
    if (year := parse_period_year(period.value)) is not None:
        return year
    match = _YEAR_RE.search(period.label) or _YEAR_RE.search(period.value)
    return int(match.group()) if match else None


def _oldest_period(periods: list[ExportPeriod]) -> str:
    """Return the period reaching furthest back, i.e. "import everything".

    ista's own ordering is not guaranteed, so a year parsed out of the label is
    preferred; if no entry carries one, the last item is the usual position of
    the oldest period in a newest-first dropdown.
    """
    dated = [(year, p) for p in periods if (year := _period_year(p)) is not None]
    if dated:
        return min(dated, key=lambda item: item[0])[1].value
    return periods[-1].value


def _heating_years(readings: list[Reading], fallback: date) -> list[int]:
    """Heating years covered by the imported readings, newest first."""
    years = {heating_year_for_date(reading.day) for reading in readings}
    if not years:
        years = {heating_year_for_date(fallback)}
    return sorted(years, reverse=True)


def _year_field(start_year: int) -> str:
    """Schema key for a heating year; also its label, as HA shows raw keys."""
    return f"{start_year}/{start_year + 1}"


class IstaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ista Online."""

    VERSION = 1

    _username: str
    _password: str
    _discovered: list[DiscoveredMeter]
    _cons_id: str
    _meter_type: str
    _periods: list[ExportPeriod]
    _readings: list[Reading]
    # Empty means "let ista pick", which is the behaviour without a history step.
    _from_period: str = ""

    @property
    def _entry_data(self) -> dict[str, Any]:
        return {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_CONS_ID: self._cons_id,
            CONF_METER_TYPE: self._meter_type,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for credentials, then auto-discover the account's meters."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            client = IstaApiClient(
                self.hass, username=self._username, password=self._password
            )
            try:
                self._discovered = await client.async_discover_meters()
            except IstaAuthError:
                errors["base"] = "invalid_auth"
            except IstaConnectionError:
                errors["base"] = "cannot_connect"
            except IstaError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during ista meter discovery")
                errors["base"] = "unknown"
            else:
                if len(self._discovered) == 1:
                    meter = self._discovered[0]
                    return await self._async_meter_chosen(
                        meter.cons_id, meter.meter_type
                    )
                if len(self._discovered) > 1:
                    return await self.async_step_meter()
                return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick among several auto-discovered meters."""
        if user_input is not None:
            cons_id = user_input[CONF_CONS_ID]
            meter_type = next(
                (m.meter_type for m in self._discovered if m.cons_id == cons_id),
                DEFAULT_METER_TYPE,
            )
            return await self._async_meter_chosen(cons_id, meter_type)

        options = {m.cons_id: f"{m.meter_type} – {m.cons_id}" for m in self._discovered}
        return self.async_show_form(
            step_id="meter",
            data_schema=vol.Schema({vol.Required(CONF_CONS_ID): vol.In(options)}),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fall back to manual cons_id entry if nothing was auto-discovered."""
        if user_input is not None:
            return await self._async_meter_chosen(
                user_input[CONF_CONS_ID],
                user_input.get(CONF_METER_TYPE, DEFAULT_METER_TYPE),
            )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONS_ID): str,
                    vol.Required(CONF_METER_TYPE, default=DEFAULT_METER_TYPE): str,
                }
            ),
        )

    async def _async_meter_chosen(
        self, cons_id: str, meter_type: str
    ) -> ConfigFlowResult:
        """Record the chosen meter and move on, aborting on a duplicate entry."""
        self._cons_id = cons_id
        self._meter_type = meter_type
        await self.async_set_unique_id(cons_id)
        self._abort_if_unique_id_configured()
        return await self.async_step_history()

    async def async_step_history(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how far back to import, then do the import."""
        errors: dict[str, str] = {}

        if user_input is None:
            try:
                self._periods = await _client_from(
                    self.hass, self._entry_data
                ).async_discover_periods()
            except IstaError as err:
                _LOGGER.warning("Could not read ista's period list: %s", err)
                self._periods = []
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error reading ista's period list")
                self._periods = []
        else:
            self._from_period = user_input.get(CONF_FROM_PERIOD, "")

        # With no readable period list there is nothing to choose, so import
        # ista's own default range straight away.
        if (user_input is not None or not self._periods) and await self._async_import(
            errors
        ):
            return await self.async_step_prices()

        return self._show_history_form(errors)

    def _show_history_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Render the history form; empty when ista offers no period list."""
        schema: dict = {}
        if self._periods:
            schema[
                vol.Required(
                    CONF_FROM_PERIOD,
                    default=self._from_period or _oldest_period(self._periods),
                )
            ] = vol.In({p.value: p.label for p in self._periods})

        return self.async_show_form(
            step_id="history",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={CONF_CONS_ID: self._cons_id},
        )

    async def _async_import(self, errors: dict[str, str]) -> bool:
        """Fetch the chosen range, filling ``errors`` on failure.

        This doubles as credential validation, so no separate check is needed.
        """
        client = _client_from(
            self.hass, self._entry_data, from_period=self._from_period
        )
        try:
            self._readings = await client.async_fetch()
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
            return True
        return False

    async def async_step_prices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the unit price of every imported heating year and finish."""
        years = _heating_years(self._readings, dt_util.now().date())

        if user_input is not None:
            prices = [
                make_period(year, float(user_input.get(_year_field(year), 0.0)))
                for year in years
            ]
            return self.async_create_entry(
                title=f"ista Online ({self._cons_id})",
                data={**self._entry_data, CONF_FROM_PERIOD: self._from_period},
                options={CONF_PRICES: prices},
            )

        return self.async_show_form(
            step_id="prices",
            data_schema=vol.Schema(
                {
                    vol.Required(_year_field(year), default=0.0): vol.Coerce(float)
                    for year in years
                }
            ),
            description_placeholders={
                CONF_CONS_ID: self._cons_id,
                CONF_METER_TYPE: self._meter_type,
                "days": str(len({r.day for r in self._readings})),
                "meters": str(len({r.meter_id for r in self._readings})),
            },
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
    """Manage prices, meter names and how much history is imported."""

    _periods: list[ExportPeriod]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["prices", "meter_names", "history"],
        )

    async def async_step_prices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or update the price for a heating year."""
        prices: list[dict] = list(self.config_entry.options.get(CONF_PRICES, []))

        if user_input is not None:
            start_year = int(user_input["start_year"])
            price = float(user_input[CONF_PRICE])
            new_prices = upsert_period(prices, start_year, price)
            return self._async_save({CONF_PRICES: new_prices})

        current_year = heating_year_for_date(dt_util.now().date())
        default_price = find_price(prices, dt_util.now().date()) or 0.0

        return self.async_show_form(
            step_id="prices",
            data_schema=vol.Schema(
                {
                    vol.Required("start_year", default=current_year): vol.Coerce(int),
                    vol.Required(CONF_PRICE, default=default_price): vol.Coerce(float),
                }
            ),
            description_placeholders={"prices": _format_prices(prices)},
        )

    async def async_step_meter_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename the meters; the names drive both devices and statistics."""
        current = self._current_meter_names()
        if not current:
            return self.async_abort(reason="no_meters")

        if user_input is not None:
            names = {
                meter_id: value.strip()
                for meter_id, value in user_input.items()
                if value and value.strip()
            }
            return self._async_save({CONF_METER_NAMES: names})

        return self.async_show_form(
            step_id="meter_names",
            data_schema=vol.Schema(
                {
                    vol.Optional(meter_id, default=name): str
                    for meter_id, name in current.items()
                }
            ),
        )

    async def async_step_history(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-import from a different starting period.

        Readings are merged on (meter, day), so choosing an older period only
        adds history — nothing already imported is lost.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_FROM_PERIOD: user_input[CONF_FROM_PERIOD],
                },
            )
            return self.async_create_entry(title="", data=dict(self.config_entry.options))

        try:
            self._periods = await _client_from(
                self.hass, dict(self.config_entry.data)
            ).async_discover_periods()
        except IstaAuthError:
            errors["base"] = "invalid_auth"
        except IstaError:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error reading ista's period list")
            errors["base"] = "unknown"

        if not self._periods:
            return self.async_abort(reason="no_periods")

        current = self.config_entry.data.get(CONF_FROM_PERIOD, "")
        return self.async_show_form(
            step_id="history",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FROM_PERIOD,
                        default=current or _oldest_period(self._periods),
                    ): vol.In({p.value: p.label for p in self._periods})
                }
            ),
            errors=errors,
        )

    def _current_meter_names(self) -> dict[str, str]:
        """Meter names as they are today, from the running coordinator."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is None:
            return dict(self.config_entry.options.get(CONF_METER_NAMES, {}))
        return dict(coordinator.meter_names)

    def _async_save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        """Persist option changes without dropping the other option keys."""
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **changes}
        )


def _format_prices(prices: list[dict]) -> str:
    """Human-readable summary of the stored price periods."""
    if not prices:
        return "(ingen priser endnu)"
    lines = []
    for period in sorted(prices, key=lambda p: p["start"]):
        lines.append(f"{period['start']} → {period['end']}: {period[CONF_PRICE]} DKK")
    return "\n".join(lines)

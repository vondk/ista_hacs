"""The ista Online (DK) integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    IstaApiClient,
    IstaAuthError,
    IstaConnectionError,
    IstaError,
    Reading,
)
from .const import (
    CONF_CONS_ID,
    CONF_FROM_PERIOD,
    CONF_METER_NAMES,
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICES,
    CONF_TO_PERIOD,
    CONF_USERNAME,
    DEFAULT_METER_TYPE,
    DOMAIN,
    STORAGE_VERSION,
)
from .names import resolve_meter_names
from .prices import find_price
from .statistics import async_push_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

UPDATE_INTERVAL = timedelta(hours=12)

type IstaConfigEntry = ConfigEntry["IstaDataUpdateCoordinator"]


@dataclass
class MeterSummary:
    """Per-meter state exposed to sensors after each update."""

    meter_id: str
    name: str
    latest_day: date | None
    total_value: float
    total_cost: float


@dataclass
class IstaSummary:
    """Lightweight state exposed to sensors after each update."""

    latest_day: date | None
    total_value: float
    meter_count: int
    meters: dict[str, MeterSummary]


def _reading_to_dict(reading: Reading) -> dict:
    return {
        "meter_id": reading.meter_id,
        "room": reading.room,
        "day": reading.day.isoformat(),
        "value": reading.value,
    }


def _reading_from_dict(data: dict) -> Reading | None:
    try:
        return Reading(
            meter_id=str(data["meter_id"]),
            room=str(data.get("room") or data["meter_id"]),
            day=date.fromisoformat(data["day"]),
            value=float(data["value"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


async def async_setup_entry(hass: HomeAssistant, entry: IstaConfigEntry) -> bool:
    """Set up ista Online from a config entry."""
    client = IstaApiClient(
        hass,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        cons_id=entry.data[CONF_CONS_ID],
        meter_type=entry.data.get(CONF_METER_TYPE, DEFAULT_METER_TYPE),
        from_period=entry.data.get(CONF_FROM_PERIOD, ""),
        to_period=entry.data.get(CONF_TO_PERIOD, ""),
    )
    store: Store[list[dict]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}_readings_{entry.entry_id}"
    )

    coordinator = IstaDataUpdateCoordinator(hass, entry, client, store)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: IstaConfigEntry) -> None:
    """Reload the entry when options (e.g. prices) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IstaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


class IstaDataUpdateCoordinator(DataUpdateCoordinator[IstaSummary]):
    """Fetch ista data, persist it, and push it into statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IstaConfigEntry,
        client: IstaApiClient,
        store: Store[list[dict]],
    ) -> None:
        """Initialize."""
        self.client = client
        self.store = store
        self._readings: dict[str, Reading] = {}
        self._loaded = False
        self._pushed = False
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )

    async def _async_load_store(self) -> None:
        if self._loaded:
            return
        stored = await self.store.async_load() or []
        for item in stored:
            reading = _reading_from_dict(item)
            if reading is not None:
                self._readings[reading.key] = reading
        self._loaded = True

    async def _async_update_data(self) -> IstaSummary:
        await self._async_load_store()

        try:
            fetched = await self.client.async_fetch()
        except IstaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IstaConnectionError as err:
            if self._readings:
                _LOGGER.info("ista unreachable — keeping last known data (%s)", err)
                return self._build_summary()
            raise UpdateFailed(str(err)) from err
        except IstaError as err:
            raise UpdateFailed(str(err)) from err

        # Merge new readings into the persisted history (keyed on meter+day).
        # A corrected value for a day we already know must be persisted too, so
        # compare the whole reading rather than just the key.
        changed = False
        for reading in fetched:
            if self._readings.get(reading.key) != reading:
                changed = True
            self._readings[reading.key] = reading

        if changed:
            await self.store.async_save(
                [_reading_to_dict(r) for r in self._readings.values()]
            )

        # Rebuilding the full history is expensive (years x meters x days), so
        # only push when something actually changed. Option changes (prices,
        # names) reload the entry, which gives a fresh coordinator with
        # ``_pushed`` reset, so they still take effect.
        if changed or not self._pushed:
            async_push_statistics(
                self.hass,
                list(self._readings.values()),
                self._prices,
                self.meter_names,
            )
            self._pushed = True

        return self._build_summary()

    @property
    def _prices(self) -> list[dict]:
        return self.config_entry.options.get(CONF_PRICES, [])

    @property
    def meter_names(self) -> dict[str, str]:
        """Display name per meter, honouring user-supplied aliases."""
        aliases = self.config_entry.options.get(CONF_METER_NAMES, {})
        return resolve_meter_names(self._readings.values(), aliases)

    def _build_summary(self) -> IstaSummary:
        readings = list(self._readings.values())
        latest_day = max((r.day for r in readings), default=None)
        total = sum(r.value for r in readings)

        prices = self._prices
        names = self.meter_names
        meters: dict[str, MeterSummary] = {}
        for reading in readings:
            meter = meters.get(reading.meter_id)
            if meter is None:
                meter = meters[reading.meter_id] = MeterSummary(
                    meter_id=reading.meter_id,
                    name=names.get(reading.meter_id, reading.meter_id),
                    latest_day=None,
                    total_value=0.0,
                    total_cost=0.0,
                )
            meter.total_value += reading.value
            price = find_price(prices, reading.day)
            if price is not None:
                meter.total_cost += reading.value * price
            if meter.latest_day is None or reading.day > meter.latest_day:
                meter.latest_day = reading.day

        for meter in meters.values():
            meter.total_value = round(meter.total_value, 3)
            meter.total_cost = round(meter.total_cost, 2)

        return IstaSummary(
            latest_day=latest_day,
            total_value=round(total, 3),
            meter_count=len(meters),
            meters=meters,
        )

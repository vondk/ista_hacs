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
    CONF_METER_TYPE,
    CONF_PASSWORD,
    CONF_PRICES,
    CONF_USERNAME,
    DEFAULT_METER_TYPE,
    DOMAIN,
    STORAGE_VERSION,
)
from .statistics import async_push_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]

UPDATE_INTERVAL = timedelta(hours=12)

type IstaConfigEntry = ConfigEntry["IstaDataUpdateCoordinator"]


@dataclass
class IstaSummary:
    """Lightweight state exposed to sensors after each update."""

    latest_day: date | None
    total_value: float
    meter_count: int


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
        changed = False
        for reading in fetched:
            if reading.key not in self._readings:
                changed = True
            self._readings[reading.key] = reading

        if changed:
            await self.store.async_save(
                [_reading_to_dict(r) for r in self._readings.values()]
            )

        prices = self.config_entry.options.get(CONF_PRICES, [])
        async_push_statistics(self.hass, list(self._readings.values()), prices)

        return self._build_summary()

    def _build_summary(self) -> IstaSummary:
        readings = list(self._readings.values())
        latest_day = max((r.day for r in readings), default=None)
        meter_ids = {r.meter_id for r in readings}
        total = sum(r.value for r in readings)
        return IstaSummary(
            latest_day=latest_day,
            total_value=round(total, 3),
            meter_count=len(meter_ids),
        )

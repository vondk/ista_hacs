"""Diagnostic sensors for the ista Online integration.

The consumption history lives in external statistics; these entities just expose
useful live state (latest reading date and the accumulated total) for cards and
automations.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import IstaConfigEntry, IstaDataUpdateCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IstaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ista Online sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            IstaLatestReadingSensor(coordinator, entry),
            IstaTotalConsumptionSensor(coordinator, entry),
        ]
    )


class IstaBaseSensor(CoordinatorEntity[IstaDataUpdateCoordinator], SensorEntity):
    """Common base for ista sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: IstaDataUpdateCoordinator, entry: IstaConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="ista Online",
            manufacturer="ista",
            configuration_url="https://www.istaonline.dk",
        )


class IstaLatestReadingSensor(IstaBaseSensor):
    """Timestamp of the newest reading from ista."""

    _attr_translation_key = "latest_reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: IstaDataUpdateCoordinator, entry: IstaConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_latest_reading"

    @property
    def native_value(self) -> datetime | None:
        """Return the newest reading date as a local-midnight datetime."""
        day = self.coordinator.data.latest_day
        if day is None:
            return None
        return dt_util.start_of_local_day(day)


class IstaTotalConsumptionSensor(IstaBaseSensor):
    """Accumulated total consumption in HCA units."""

    _attr_translation_key = "total_consumption"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "enheder"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:radiator"

    def __init__(
        self, coordinator: IstaDataUpdateCoordinator, entry: IstaConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_total_consumption"

    @property
    def native_value(self) -> float | None:
        """Return the accumulated total consumption."""
        return self.coordinator.data.total_value

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Expose the number of meters seen."""
        return {"meter_count": self.coordinator.data.meter_count}

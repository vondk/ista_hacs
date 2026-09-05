"""Sensors for the ista Online integration.

Two diagnostic entities describe the account as a whole, and every meter gets
its own device with consumption, cost and last-reading entities. The full
historical series lives in external statistics (see :mod:`.statistics`); these
entities expose live state for cards and automations.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import IstaConfigEntry, IstaDataUpdateCoordinator, MeterSummary
from .const import CONF_METER_TYPE, DEFAULT_METER_TYPE, DOMAIN, STAT_COST_UNIT

# HCA meters count dimensionless "units", which is also what the account-wide
# total sensor has always reported.
CONSUMPTION_UNIT = "enheder"

# Readable model names for the meter types ista exposes; the raw code is kept
# as the model id.
METER_MODELS = {"HCA": "Varmefordelingsmåler"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IstaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ista Online sensors."""
    coordinator = entry.runtime_data

    # Created up front (rather than left to the base sensors' DeviceInfo) so its
    # id is available immediately for the per-meter devices' via_device_id --
    # async_add_entities() does not create devices synchronously, so relying on
    # it would race the first call to _async_add_new_meters() below.
    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=DeviceEntryType.SERVICE,
        name="ista Online",
        manufacturer="ista",
        configuration_url="https://www.istaonline.dk",
    )

    async_add_entities(
        [
            IstaLatestReadingSensor(coordinator, entry),
            IstaTotalConsumptionSensor(coordinator, entry),
        ]
    )

    known: set[str] = set()

    @callback
    def _async_add_new_meters() -> None:
        """Create entities for meters that appeared since the last update."""
        new = [
            meter_id
            for meter_id in coordinator.data.meters
            if meter_id not in known
        ]
        if not new:
            return
        known.update(new)
        entities: list[IstaMeterSensor] = []
        for meter_id in new:
            entities.extend(
                (
                    IstaMeterConsumptionSensor(coordinator, entry, meter_id, hub_device.id),
                    IstaMeterCostSensor(coordinator, entry, meter_id, hub_device.id),
                    IstaMeterLatestReadingSensor(coordinator, entry, meter_id, hub_device.id),
                )
            )
        async_add_entities(entities)

    _async_add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_meters))


class IstaBaseSensor(CoordinatorEntity[IstaDataUpdateCoordinator], SensorEntity):
    """Common base for account-wide ista sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: IstaDataUpdateCoordinator, entry: IstaConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
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
    _attr_native_unit_of_measurement = CONSUMPTION_UNIT
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


class IstaMeterSensor(CoordinatorEntity[IstaDataUpdateCoordinator], SensorEntity):
    """Base for entities belonging to a single meter."""

    _attr_has_entity_name = True
    _key: str

    def __init__(
        self,
        coordinator: IstaDataUpdateCoordinator,
        entry: IstaConfigEntry,
        meter_id: str,
        hub_device_id: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_unique_id = f"{entry.entry_id}_{meter_id}_{self._key}"
        meter = coordinator.data.meters.get(meter_id)
        meter_type = entry.data.get(CONF_METER_TYPE, DEFAULT_METER_TYPE)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{meter_id}")},
            via_device_id=hub_device_id,
            name=meter.name if meter else meter_id,
            manufacturer="ista",
            model=METER_MODELS.get(meter_type, meter_type),
            model_id=meter_type,
            serial_number=meter_id,
            configuration_url="https://www.istaonline.dk",
        )

    @property
    def meter(self) -> MeterSummary | None:
        """Return this meter's latest summary, if it is still reported."""
        return self.coordinator.data.meters.get(self._meter_id)

    @property
    def available(self) -> bool:
        """Only available while the meter is present in the data."""
        return super().available and self.meter is not None


class IstaMeterConsumptionSensor(IstaMeterSensor):
    """Accumulated consumption for one meter."""

    _key = "consumption"
    _attr_translation_key = "meter_consumption"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = CONSUMPTION_UNIT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:radiator"

    @property
    def native_value(self) -> float | None:
        """Return the accumulated consumption for this meter."""
        meter = self.meter
        return meter.total_value if meter else None


class IstaMeterCostSensor(IstaMeterSensor):
    """Accumulated cost for one meter."""

    _key = "cost"
    _attr_translation_key = "meter_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = STAT_COST_UNIT
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return the accumulated cost for this meter."""
        meter = self.meter
        return meter.total_cost if meter else None


class IstaMeterLatestReadingSensor(IstaMeterSensor):
    """Timestamp of the newest reading for one meter."""

    _key = "latest_reading"
    _attr_translation_key = "meter_latest_reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        """Return this meter's newest reading date as a local-midnight datetime."""
        meter = self.meter
        if meter is None or meter.latest_day is None:
            return None
        return dt_util.start_of_local_day(meter.latest_day)

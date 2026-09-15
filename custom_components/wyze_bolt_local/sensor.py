from homeassistant.components.bluetooth import async_last_service_info
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WyzeBoltCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WyzeBoltBatterySensor(coordinator),
            WyzeBoltRssiSensor(coordinator),
        ]
    )


class WyzeBoltBatterySensor(CoordinatorEntity[WyzeBoltCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "battery"
    _attr_name = "Battery"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}-battery"

    @property
    def native_value(self):
        return self.coordinator.data.get("battery")

    @property
    def available(self):
        return self.coordinator.available and bool(self.coordinator.data.get("available", False))

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.mac)},
            "name": self.coordinator.config_entry.data.get("name", "Wyze Lock Bolt"),
            "manufacturer": "Wyze",
            "model": "Lock Bolt V1 / YD_BT1",
            "connections": {("bluetooth", self.coordinator.mac)},
        }


class WyzeBoltRssiSensor(CoordinatorEntity[WyzeBoltCoordinator], SensorEntity):
    """Bluetooth signal strength, sourced from HA's advertisement data.

    No extra BLE traffic -- useful for placement/range debugging.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "signal_strength"
    _attr_name = "Signal strength"
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}-rssi"

    @property
    def native_value(self):
        info = async_last_service_info(
            self.coordinator.hass, self.coordinator.mac, connectable=True
        )
        return info.rssi if info is not None else None

    @property
    def available(self):
        return self.coordinator.available

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.mac)},
            "name": self.coordinator.config_entry.data.get("name", "Wyze Lock Bolt"),
            "manufacturer": "Wyze",
            "model": "Lock Bolt V1 / YD_BT1",
            "connections": {("bluetooth", self.coordinator.mac)},
        }

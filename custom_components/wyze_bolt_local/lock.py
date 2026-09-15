from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WyzeBoltCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            WyzeBoltLock(coordinator),
        ]
    )


class WyzeBoltLock(
    CoordinatorEntity[WyzeBoltCoordinator],
    LockEntity,
):
    _attr_has_entity_name = True
    _attr_translation_key = "bolt"
    _attr_name = "Lock"

    def __init__(
        self,
        coordinator: WyzeBoltCoordinator,
    ) -> None:
        super().__init__(coordinator)

        self._attr_unique_id = f"{coordinator.mac}-lock"

    @property
    def device_info(self):
        info = {
            "identifiers": {
                (DOMAIN, self.coordinator.mac),
            },
            "name": self.coordinator.config_entry.data.get(
                "name",
                "Wyze Lock Bolt",
            ),
            "manufacturer": "Wyze",
            "model": "Lock Bolt V1 / YD_BT1",
            "connections": {
                ("bluetooth", self.coordinator.mac),
            },
        }
        info.update(self.coordinator.device_info_extra)
        return info

    @property
    def is_locked(self):
        state = self.coordinator.data.get("state")

        if state is None:
            return None

        return state == 1

    async def async_lock(self, **kwargs):
        await self.coordinator.lock_unlock("lock")

    async def async_unlock(self, **kwargs):
        await self.coordinator.lock_unlock("unlock")

    @property
    def available(self):
        return (
            self.coordinator.available
            and bool(
                self.coordinator.data.get(
                    "available",
                    False,
                )
            )
        )

    @property
    def extra_state_attributes(self):
        """Expose timestamps useful for debugging right on the entity."""
        data = self.coordinator.data
        attrs = {
            "mac_address": self.coordinator.mac,
        }
        if timestamp := data.get("timestamp"):
            attrs["last_state_notify"] = timestamp.isoformat()
        if battery_timestamp := data.get("battery_timestamp"):
            attrs["last_battery_update"] = battery_timestamp.isoformat()
        return attrs

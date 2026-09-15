from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WyzeBoltCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            WyzeBoltRefreshButton(coordinator),
        ]
    )


class WyzeBoltRefreshButton(
    CoordinatorEntity[WyzeBoltCoordinator],
    ButtonEntity,
):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_name = "Refresh now"

    def __init__(self, coordinator: WyzeBoltCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}-refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        # Always available: forcing a refresh is exactly how you retry a
        # failed poll instead of waiting for the next timer tick.
        return True

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.mac)},
            "name": self.coordinator.config_entry.data.get(
                "name",
                "Wyze Lock Bolt",
            ),
            "manufacturer": "Wyze",
            "model": "Lock Bolt V1 / YD_BT1",
            "connections": {("bluetooth", self.coordinator.mac)},
        }

from .const import DOMAIN
from .coordinator import WyzeBoltCoordinator

PLATFORMS = ["button", "lock", "sensor"]


async def async_setup_entry(hass, entry):
    coordinator = WyzeBoltCoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Convert a failed first poll into ConfigEntryNotReady so HA retries
    # setup automatically once Bluetooth discovery catches up. A plain
    # async_refresh() would silently leave all entities unavailable forever.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass, entry):
    """Reload the entry when its options (e.g. poll interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    return unload_ok

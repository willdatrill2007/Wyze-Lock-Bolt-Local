from datetime import datetime

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_OPERATE_KEY, CONF_STATE_KEY, DOMAIN

TO_REDACT = {CONF_STATE_KEY, CONF_OPERATE_KEY}


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def async_get_config_entry_diagnostics(hass, entry):
    """Return diagnostics for a config entry, with keys redacted."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
        },
        "coordinator": {
            "persistent": coordinator.persistent,
            "keepalive_interval": coordinator.keepalive,
            "device_info": coordinator.device_info_extra,
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": coordinator.update_interval.total_seconds(),
            "data": {key: _serialize(value) for key, value in coordinator.data.items()},
        },
    }

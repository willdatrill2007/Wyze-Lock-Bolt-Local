import asyncio
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_BLE_ID,
    CONF_MAC,
    CONF_OPERATE_KEY,
    CONF_KEEPALIVE,
    CONF_POLL_INTERVAL,
    CONF_PERSISTENT,
    CONF_STATE_KEY,
    DEFAULT_KEEPALIVE,
    DEFAULT_PERSISTENT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    POLL_INTERVAL_OPTIONS,
)
from .coordinator import CannotConnect, InvalidKey, async_validate_lock

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_mac: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ):
        """Handle a Wyze Lock Bolt discovered via Bluetooth."""
        mac = discovery_info.address.upper()
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        self._discovered_mac = mac
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input=None):
        """Confirm the discovery, then collect the keys."""
        if user_input is not None:
            return await self.async_step_user()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": "Wyze Lock Bolt",
                "mac": self._discovered_mac or "",
            },
        )

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            mac = user_input[CONF_MAC].strip().upper()
            if len(mac.split(":")) != 6:
                errors["base"] = "invalid_mac"
            elif (
                len(user_input[CONF_STATE_KEY]) != 16
                or len(user_input[CONF_OPERATE_KEY]) != 16
                or not user_input[CONF_STATE_KEY].isascii()
                or not user_input[CONF_OPERATE_KEY].isascii()
            ):
                errors["base"] = "invalid_key"
            else:
                # Validate connectivity + state key before creating the entry,
                # so a typo or an out-of-range lock fails setup immediately
                # with a clear error instead of an unavailable entity later.
                try:
                    await asyncio.wait_for(
                        async_validate_lock(
                            self.hass,
                            mac,
                            user_input[CONF_STATE_KEY],
                            user_input[CONF_OPERATE_KEY],
                            int(user_input[CONF_BLE_ID]),
                        ),
                        timeout=30,
                    )
                except InvalidKey:
                    errors["base"] = "invalid_state_key"
                except (CannotConnect, asyncio.TimeoutError):
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error validating Wyze Lock Bolt")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured()
                    data = {
                        CONF_MAC: mac,
                        CONF_STATE_KEY: user_input[CONF_STATE_KEY],
                        CONF_OPERATE_KEY: user_input[CONF_OPERATE_KEY],
                        CONF_BLE_ID: int(user_input[CONF_BLE_ID]),
                        CONF_NAME: user_input.get(CONF_NAME, "Wyze Lock Bolt"),
                    }
                    return self.async_create_entry(title=data[CONF_NAME], data=data)

        schema = vol.Schema({
            vol.Required(CONF_NAME, default="Wyze Lock Bolt"): str,
            vol.Required(
                CONF_MAC,
                default=self._discovered_mac or vol.UNDEFINED,
            ): str,
            vol.Required(CONF_BLE_ID, default=1001): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Required(CONF_STATE_KEY): str,
            vol.Required(CONF_OPERATE_KEY): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        persistent = self.config_entry.options.get(
            CONF_PERSISTENT, DEFAULT_PERSISTENT
        )
        keepalive = self.config_entry.options.get(
            CONF_KEEPALIVE, DEFAULT_KEEPALIVE
        )

        schema = vol.Schema({
            vol.Required(CONF_POLL_INTERVAL, default=current): vol.In(
                POLL_INTERVAL_OPTIONS
            ),
            vol.Required(CONF_PERSISTENT, default=persistent): bool,
            vol.Required(CONF_KEEPALIVE, default=keepalive): vol.In(
                [0, 2, 3, 4, 5, 10]
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors={})

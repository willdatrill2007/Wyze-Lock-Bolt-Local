# coordinator.py
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
    async_last_service_info,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BATTERY_LEVEL_UUID,
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
    STATE_UUID,
    UART_RX_UUID,
    UART_TX_UUID,
)
from .crypto import decrypt_state_or_battery
from .protocol import (
    extract_l1_frames,
    pack_ack,
    pack_challenge_request,
    pack_lock_unlock,
    parse_l2_dict,
)

_LOGGER = logging.getLogger(__name__)

try:
    _ESTABLISH_SUPPORTS_CB = (
        "disconnected_callback"
        in inspect.signature(establish_connection).parameters
    )
except (TypeError, ValueError):  # pragma: no cover - exotic builds
    _ESTABLISH_SUPPORTS_CB = False

# Seconds to wait for the lock's BLE advertisement to reappear before
# declaring a poll failure (advertisements can briefly vanish right after
# a disconnect, especially via Bluetooth proxies).
VISIBILITY_TIMEOUT = 8.0

# Seconds after the last successful poll during which transient poll
# failures keep the entities marked available (no unavailability flaps).
AVAILABILITY_GRACE = 60.0


class WyzeBoltCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, config_entry) -> None:
        poll_seconds = config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        self.persistent = bool(
            config_entry.options.get(CONF_PERSISTENT, DEFAULT_PERSISTENT)
        )
        self.keepalive = int(
            config_entry.options.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE)
        )
        super().__init__(
            hass,
            _LOGGER,
            name="Wyze Lock Bolt Local",
            update_interval=timedelta(seconds=poll_seconds),
        )
        self.hass = hass
        self.config_entry = config_entry
        self.mac = config_entry.data[CONF_MAC].upper()
        self.state_key = config_entry.data[CONF_STATE_KEY]
        self.operate_key = config_entry.data[CONF_OPERATE_KEY]
        self.ble_id = int(config_entry.data[CONF_BLE_ID])
        self._client: BleakClient | None = None
        self._uart_buffer = bytearray()
        self._frames: asyncio.Queue[tuple[int, int, bytes]] = asyncio.Queue()
        self._state_event = asyncio.Event()
        self._command_lock = asyncio.Lock()
        self._command_pending = asyncio.Event()
        self._last_command_at: float | None = None
        self._last_success_at: float | None = None
        self._command_epoch = 0
        self._subscribed = False
        self._reconnect_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._last_uart_chunk: bytes | None = None
        self._last_uart_chunk_at = 0.0
        self._last_state_chunk: bytes | None = None
        self._last_state_chunk_at = 0.0
        self._device_info_fetched = False
        self.device_info_extra: dict[str, str] = {}

        self.data = {
            "state": None,
            "timestamp": None,
            "battery": None,
            "battery_timestamp": None,
            "available": False,
        }

    async def _get_client(self) -> BleakClient:
        if (
            self.persistent
            and self._client is not None
            and self._client.is_connected
        ):
            return self._client
        # Tear down any previous client object first. Without this, a stale
        # client whose BlueZ link is still (zombie-)alive keeps its
        # notification registrations, the lock ends up with several
        # concurrent links, and duplicate packets from all of them feed the
        # shared reassembly buffer (observed as Nx duplicate deliveries and
        # CRC mismatches).
        old_client = self._client
        if old_client is not None:
            self._client = None
            self._subscribed = False
            try:
                await old_client.disconnect()
            except Exception:
                pass
        # Right after a disconnect the advertisement can briefly vanish from
        # the Bluetooth stack / proxy, which used to cause instant 0.1s poll
        # failures and availability flaps. Wait a few seconds for the lock to
        # re-advertise before giving up.
        deadline = time.monotonic() + VISIBILITY_TIMEOUT
        device = None
        while True:
            device = async_ble_device_from_address(
                self.hass, self.mac, connectable=True
            )
            if device is not None:
                break
            service_info = async_last_service_info(
                self.hass, self.mac, connectable=True
            )
            if service_info is not None:
                _LOGGER.debug("Using last known advertisement for %s", self.mac)
                device = service_info.device
                break
            if time.monotonic() >= deadline:
                _LOGGER.debug(
                    "Visible BLE devices: %s",
                    [i.address for i in async_discovered_service_info(self.hass)],
                )
                raise UpdateFailed(
                    f"Wyze Lock Bolt {self.mac} is not currently visible to Home Assistant Bluetooth"
                )
            await asyncio.sleep(0.5)
        kwargs = {}
        if _ESTABLISH_SUPPORTS_CB:
            kwargs["disconnected_callback"] = self._on_client_disconnect
        # max_attempts=1: fail fast and let our own retry/backoff layers
        # (poll retry, reconnect loop, 60s availability grace) handle it.
        # The library's internal multi-attempt retries produced 45-50s
        # failed fetches and piled onto an already-busy lock.
        self._client = await establish_connection(
            BleakClient, device, device.address, max_attempts=1, **kwargs
        )
        self._subscribed = False
        self._maybe_start_keepalive()
        return self._client

    def _maybe_start_keepalive(self) -> None:
        """Start the periodic keepalive read (persistent mode only)."""
        if (
            self.persistent
            and self.keepalive > 0
            and (self._keepalive_task is None or self._keepalive_task.done())
        ):
            self._keepalive_task = self.hass.async_create_task(
                self._keepalive_loop()
            )

    async def _keepalive_loop(self) -> None:
        """Persistent-mode keepalive: periodic state reads.

        Two jobs: keep the link's supervision timer fed (the lock drops
        idle connections after ~5s -- the negotiated supervision timeout),
        and pick up manual state changes quickly between notifications.
        """
        while self.persistent and self.keepalive > 0:
            await asyncio.sleep(self.keepalive)
            client = self._client
            if client is None or not client.is_connected:
                # Drop handler will start a fresh keepalive on reconnect.
                return
            if self._command_lock.locked():
                # A poll or command is using the link; skip this tick rather
                # than interleave GATT traffic (BlueZ InProgress errors).
                continue
            try:
                raw = await client.read_gatt_char(STATE_UUID)
                state, ts = decrypt_state_or_battery(
                    self.state_key, bytes(raw)
                )
            except Exception as err:
                _LOGGER.debug("Keepalive read failed: %s", err)
                await self._drop_client()
                return
            self.data = {
                **self.data,
                "state": state,
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
            }
            self._last_success_at = time.monotonic()
            self.async_set_updated_data(self.data)

    def _on_client_disconnect(self, client: BleakClient | None = None) -> None:
        # bleak >= 0.20 passes the client that disconnected; it may also be
        # invoked from a D-Bus worker thread, so hop to the event loop.
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect, client)

    def _handle_disconnect(self, client: BleakClient | None = None) -> None:
        if client is not None and client is not self._client:
            # Stale client object we have already replaced; ignore.
            return
        self._client = None
        self._subscribed = False
        if not self.persistent:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            _LOGGER.info("Wyze Lock Bolt connection lost; will reconnect")
            self._reconnect_task = self.hass.async_create_task(
                self._reconnect_loop()
            )

    def _schedule_reconnect(self, delay: float) -> None:
        """Connect soon if the link is down; no-op if it is already live."""
        if not self.persistent:
            return
        if self._client is not None and self._client.is_connected:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            _LOGGER.debug("Scheduling link recovery in %.1fs", delay)
            self._reconnect_task = self.hass.async_create_task(
                self._reconnect_loop(start_delay=delay)
            )

    async def _reconnect_loop(self, start_delay: float = 1.0) -> None:
        """Persistent-mode reconnect with exponential backoff."""
        delay = start_delay
        while self.persistent:
            await asyncio.sleep(delay)
            if not self.persistent:
                return
            if self._client is not None and self._client.is_connected:
                return
            if self._command_pending.is_set():
                # A lock/unlock command is waiting for the command lock;
                # don't make it wait behind our (slow) reconnect attempt.
                delay = 3.0
                continue
            try:
                await self._poll_lock()
            except Exception as err:
                _LOGGER.debug("Reconnect poll failed: %s", err)
            else:
                _LOGGER.info("Wyze Lock Bolt connection restored")
                return
            delay = min(delay * 2, 60.0)

    async def _ensure_subscribed(self, client: BleakClient) -> None:
        """Subscribe to UART + state notifications once per connection."""
        if self._subscribed:
            return
        self._uart_buffer.clear()
        while not self._frames.empty():
            self._frames.get_nowait()
        # Filter deliveries through the client that registered them: an
        # orphaned (zombie) link can stay alive at BlueZ level and fan
        # identical notifications out per subscribed central -- feeding
        # duplicates into the shared reassembly buffer corrupts framing
        # (observed as CRC mismatches). Deliveries from any client other
        # than the current one are dropped.
        def _uart_cb(handle, data, _client=client):
            if _client is self._client:
                self._on_uart(handle, data)

        def _state_cb(handle, data, _client=client):
            if _client is self._client:
                self._on_state(handle, data)

        await client.start_notify(UART_RX_UUID, _uart_cb)
        try:
            await client.start_notify(STATE_UUID, _state_cb)
        except Exception:
            # Roll back so a retry cannot double-subscribe the UART char.
            try:
                await client.stop_notify(UART_RX_UUID)
            except Exception:
                pass
            raise
        self._subscribed = True

    async def _drop_client(self) -> None:
        """Tear down the current connection (error paths)."""
        client = self._client
        self._client = None
        self._subscribed = False
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=3.0)
        except Exception:
            pass

    async def _read_all(self) -> dict[str, Any]:
        client = await self._get_client()

        if self.persistent:
            # Subscribe once per connection so the lock can push state
            # changes (manual turns) in real time between polls.
            await self._ensure_subscribed(client)
        try:
            state_raw = await client.read_gatt_char(STATE_UUID)
            battery_raw = await client.read_gatt_char(BATTERY_LEVEL_UUID)
            state, state_ts = decrypt_state_or_battery(self.state_key, bytes(state_raw))
            battery, battery_ts = decrypt_state_or_battery(self.state_key, bytes(battery_raw))
            await self._ensure_device_info(client)
            return {
                "state": state,
                "timestamp": datetime.fromtimestamp(state_ts, tz=timezone.utc),
                "battery": battery,
                "battery_timestamp": datetime.fromtimestamp(battery_ts, tz=timezone.utc),
                "available": True,
            }
        finally:
            if not self.persistent:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _async_update_data(self) -> dict[str, Any]:
        # A lock/unlock command already refreshed the state via live
        # notification; don't burn a full BLE connect/read/disconnect cycle
        # immediately afterwards. This also prevents refreshes that queued
        # up behind a long command from hammering the lock back-to-back.
        if (
            self._last_command_at is not None
            and time.monotonic() - self._last_command_at
            < self.update_interval.total_seconds()
        ):
            _LOGGER.debug("Skipping poll; state is already fresh from a recent command")
            return self.data

        return await self._poll_lock()

    async def _poll_lock(self) -> dict[str, Any]:
        last_err: Exception | None = None
        async with self._command_lock:
            for attempt in range(2):
                try:
                    data = await self._read_all()
                except UpdateFailed:
                    raise
                except Exception as err:
                    last_err = err
                    _LOGGER.debug(
                        "Wyze Bolt poll attempt %d failed: %s", attempt + 1, err
                    )
                    if self.persistent:
                        # Don't reuse a connection that just failed.
                        await self._drop_client()
                    if attempt == 0:
                        if self._command_pending.is_set():
                            _LOGGER.debug(
                                "Skipping poll retry; a lock/unlock command is waiting"
                            )
                            break
                        await asyncio.sleep(2)
                else:
                    self._last_success_at = time.monotonic()
                    return data
        if self.persistent:
            await self._drop_client()
        raise UpdateFailed(str(last_err)) from last_err

    @property
    def available(self) -> bool:
        """Whether the lock should be considered available.

        A single failed poll (e.g. the advertisement briefly vanishing after
        a disconnect) should not flap the entities unavailable; only failures
        sustained past the grace window since the last success do.
        """
        if self.last_update_success:
            return True
        if self._last_success_at is None:
            return False
        return time.monotonic() - self._last_success_at < AVAILABILITY_GRACE

    async def async_refresh_now(self) -> None:
        await self.async_request_refresh()

    def _on_uart(self, _sender, data: bytearray) -> None:
        _LOGGER.debug("UART RX raw  (%d bytes): %s", len(data), data.hex())

        # Some adapter/firmware/BlueZ combinations deliver each notification
        # twice (observed: byte-identical chunks ~1ms apart, and even
        # disconnect callbacks firing in pairs). Identical back-to-back
        # chunks are always duplicates -- real frames carry a unique seq or
        # encrypted payload -- and feeding them into the shared reassembly
        # buffer corrupts framing (CRC mismatches, deadlocked commands).
        now = time.monotonic()
        if (
            bytes(data) == self._last_uart_chunk
            and now - self._last_uart_chunk_at < 0.05
        ):
            _LOGGER.debug("Dropping duplicate UART RX chunk")
            return
        self._last_uart_chunk = bytes(data)
        self._last_uart_chunk_at = now

        self._uart_buffer.extend(data)
        try:
            frames = extract_l1_frames(self._uart_buffer)
            for frame in frames:
                flags, seq, payload = frame
                _LOGGER.debug(
                    "UART RX frame flags=0x%02X seq=%d payload=%s",
                    flags,
                    seq,
                    payload.hex(),
                )
                self._frames.put_nowait(frame)
        except ValueError as err:
            _LOGGER.warning("Invalid Wyze UART frame: %s", err)
            self._uart_buffer.clear()

    def _on_state(self, _sender, data: bytearray) -> None:
        _LOGGER.debug("State notify (%d bytes): %s", len(data), data.hex())

        # Same duplicate-delivery environment as _on_uart (see there).
        now = time.monotonic()
        if (
            bytes(data) == self._last_state_chunk
            and now - self._last_state_chunk_at < 0.05
        ):
            return
        self._last_state_chunk = bytes(data)
        self._last_state_chunk_at = now

        try:
            state, timestamp = decrypt_state_or_battery(self.state_key, bytes(data))
        except Exception as err:
            _LOGGER.warning("Invalid Wyze state notification: %s", err)
            return
        self.data = {
            **self.data,
            "state": state,
            "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc),
            "available": True,
        }
        self._state_event.set()
        self.async_set_updated_data(self.data)

    async def _next_frame(self, timeout: float = 5.0):
        frame = await asyncio.wait_for(self._frames.get(), timeout)
        flags, seq, payload = frame
        _LOGGER.debug("Dequeued frame flags=0x%02X seq=%d", flags, seq)
        return frame

    async def _write_uart(self, data: bytes) -> None:
        _LOGGER.debug("UART TX raw  (%d bytes): %s", len(data), data.hex())
        if self._client is None:
            raise RuntimeError("Bluetooth client unexpectedly unavailable")
        await self._client.write_gatt_char(UART_TX_UUID, data, response=False)

    async def _ensure_device_info(self, client: BleakClient) -> None:
        """Read the standard Device Information service once per run.

        Feeds the device registry (firmware/hardware/serial/model) instead
        of creating throwaway sensor entities.
        """
        if self._device_info_fetched:
            return
        self._device_info_fetched = True
        chars = {
            "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
            "model": "00002a24-0000-1000-8000-00805f9b34fb",
            "serial_number": "00002a25-0000-1000-8000-00805f9b34fb",
            "sw_version": "00002a26-0000-1000-8000-00805f9b34fb",
            "hw_version": "00002a27-0000-1000-8000-00805f9b34fb",
        }
        for key, uuid in chars.items():
            try:
                raw = await client.read_gatt_char(uuid)
                value = bytes(raw).decode("utf-8", "replace").strip("\x00").strip()
            except Exception:
                continue
            if value:
                self.device_info_extra[key] = value
        if self.device_info_extra:
            _LOGGER.info("Wyze Lock Bolt device info: %s", self.device_info_extra)

    async def _connect_command(self) -> None:
        client = await self._get_client()
        await self._ensure_subscribed(client)
        # Start every command session with clean stream state. Frames left
        # over from a previous command on a persistent connection would be
        # dequeued by this command's stage waits and break the handshake
        # state machine (observed: "Unexpected challenge ACK flags=0x40"
        # from a previous command's leftover result frame).
        self._uart_buffer.clear()
        while not self._frames.empty():
            self._frames.get_nowait()
        self._state_event.clear()

    async def _disconnect_command(self) -> None:
        if self.persistent:
            # Keep the connection alive for the next poll/command; the lock
            # pushes state changes over it in the meantime.
            return
        if self._client is None:
            return
        client = self._client
        self._client = None
        self._subscribed = False

        # Disconnecting the client tears down any active notification
        # subscriptions on its own, so there's no need to call stop_notify
        # first. Calling it separately was routinely hitting its 2.0s
        # timeout and adding ~2s of dead time to every command.
        try:
            await asyncio.wait_for(client.disconnect(), timeout=3.0)
        except Exception:
            pass

    async def lock_unlock(self, command: str) -> None:
        # Coalesce bursts of service calls (e.g. a user tapping the UI while
        # a slow command is still connecting over the proxy): only the
        # newest requested command actually runs; any request superseded
        # while waiting for the command lock drops itself. Final lock state
        # is what matters, and stacking redundant cycles costs ~25s each.
        self._command_epoch += 1
        my_epoch = self._command_epoch
        t0 = time.monotonic()
        _LOGGER.debug(
            "Starting %s command (mac=%s ble_id=%d)",
            command,
            self.mac,
            self.ble_id,
        )
        self._command_pending.set()
        try:
            async with self._command_lock:
                if my_epoch != self._command_epoch:
                    _LOGGER.debug(
                        "Skipping %s command; superseded by a newer request",
                        command,
                    )
                    return
                self._command_pending.clear()
                try:
                    # Stage: connect
                    t1 = time.monotonic()
                    await self._connect_command()
                    _LOGGER.debug("Connected in %.2fs", time.monotonic() - t1)

                    if self._client is None:
                        raise RuntimeError("Bluetooth client unexpectedly unavailable")

                    # Stage 0: challenge request + ACK
                    t1 = time.monotonic()
                    await self._write_uart(pack_challenge_request())
                    flags, seq, payload = await self._next_frame()
                    if not (flags == 0x48 and seq == 1):
                        raise RuntimeError(
                            f"Unexpected challenge ACK flags={flags:02x} seq={seq}"
                        )
                    _LOGGER.debug("Challenge ACK in %.2fs", time.monotonic() - t1)

                    # Stage 1: D2 challenge
                    t1 = time.monotonic()
                    while True:
                        flags, seq, payload = await self._next_frame()
                        if flags != 0x40:
                            continue
                        cmd, _l2flags, fields = parse_l2_dict(payload)
                        if cmd == 0x86 and 0xD2 in fields:
                            challenge = fields[0xD2]
                            if len(challenge) != 16:
                                raise RuntimeError(
                                    f"Unexpected challenge length {len(challenge)}"
                                )
                            break
                    _LOGGER.debug("Challenge received in %.2fs", time.monotonic() - t1)

                    await self._write_uart(pack_ack(seq))
                    self._state_event.clear()

                    # Send command
                    t1 = time.monotonic()
                    cmd_packet = pack_lock_unlock(
                        self.ble_id, self.operate_key, challenge, command, seq=2
                    )
                    _LOGGER.debug(
                        "%s packet (ble_id=%d): %s",
                        command,
                        self.ble_id,
                        cmd_packet.hex(),
                    )
                    await self._write_uart(cmd_packet)
                    _LOGGER.debug("Command sent in %.2fs", time.monotonic() - t1)

                    # Stage 2: ACK to command (some firmware skips this).
                    got_result = False
                    try:
                        while True:
                            flags, seq2, payload = await self._next_frame(timeout=3.0)
                            if flags == 0x48 and seq2 == 2:
                                _LOGGER.debug("Received command ACK")
                                break
                            if flags == 0x40:
                                cmd, _l2flags, _fields = parse_l2_dict(payload)
                                if cmd == 0x04:
                                    _LOGGER.debug("Received command result without ACK")
                                    await self._write_uart(pack_ack(seq2))
                                    got_result = True
                                    break
                    except TimeoutError:
                        _LOGGER.debug("No command ACK received, continuing")

                    # Stage 3: command result.
                    if not got_result:
                        t1 = time.monotonic()
                        while True:
                            flags, seq3, payload = await self._next_frame()
                            if flags != 0x40:
                                _LOGGER.debug(
                                    "Stage 3: skipping non-data frame flags=0x%02X seq=%d",
                                    flags,
                                    seq3,
                                )
                                continue
                            cmd, _l2flags, fields = parse_l2_dict(payload)
                            _LOGGER.debug(
                                "Stage 3: L2 response cmd=0x%02X fields=%s",
                                cmd,
                                {k: v.hex() for k, v in fields.items()},
                            )
                            await self._write_uart(pack_ack(seq3))
                            if cmd == 0x04:
                                break
                            _LOGGER.warning(
                                "Lock returned unexpected response cmd=0x%02X to %s",
                                cmd,
                                command,
                            )
                            break
                        _LOGGER.debug("Result received in %.2fs", time.monotonic() - t1)

                    # Wait for state notification. The lock already pushes a
                    # State notify during the command exchange, which _on_state
                    # uses to update self.data and call async_set_updated_data
                    # in real time. So in the normal case we already have fresh
                    # state and do NOT need to run a second full BLE
                    # connect/read/disconnect cycle afterward.
                    got_state_notify = True
                    try:
                        await asyncio.wait_for(self._state_event.wait(), 5.0)
                    except asyncio.TimeoutError:
                        got_state_notify = False
                        _LOGGER.debug(
                            "No state notification after %s; will poll in background",
                            command,
                        )

                    # Disconnect
                    t1 = time.monotonic()
                    await self._disconnect_command()
                    _LOGGER.debug("Disconnected in %.2fs", time.monotonic() - t1)

                    if not got_state_notify:
                        # Only fall back to a full poll if we didn't get a live
                        # notification, and don't block command completion on
                        # it -- run it in the background instead.
                        self.hass.async_create_task(self.async_request_refresh())

                    if got_state_notify:
                        # Only treat the state as "command-fresh" when the
                        # lock actually pushed a new state notification.
                        # Otherwise the background poll scheduled above must
                        # really hit the lock, not get skipped by the
                        # freshness guard.
                        self._last_command_at = time.monotonic()
                    if self.persistent:
                        # Actuating the motor can brown out the lock's BLE
                        # stack, killing the link within seconds of a
                        # command. Start recovery immediately so the next
                        # command finds a live link instead of paying a
                        # ~10s reconnect against a sleeping lock.
                        self._schedule_reconnect(delay=1.5)
                    _LOGGER.info(
                        "%s completed in %.2fs total",
                        command,
                        time.monotonic() - t0,
                    )
                except TimeoutError as err:
                    await self._drop_client()
                    raise HomeAssistantError(
                        f"Wyze Lock Bolt did not respond to the {command} command in time"
                    ) from err
                except HomeAssistantError:
                    await self._drop_client()
                    raise
                except Exception as err:
                    await self._drop_client()
                    raise HomeAssistantError(
                        f"Could not {command} the Wyze Lock Bolt: {err}"
                    ) from err
        finally:
            self._command_pending.clear()

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot reach the lock."""


class InvalidKey(HomeAssistantError):
    """Error to indicate a local key does not decrypt the lock's data."""


async def async_validate_lock(
    hass,
    mac: str,
    state_key: str,
    operate_key: str,
    ble_id: int,
) -> None:
    """Validate reachability and keys at setup time, without actuating the lock.

    This connects to the lock and decrypts both readable encrypted
    characteristics (state + battery) with the state key, so a typo'd state
    key is caught immediately with a clear error instead of surfacing later
    as a failed poll.

    The operate key and ble_id cannot be verified without sending a real
    lock/unlock command, which we deliberately do not do during setup.
    """
    device = async_ble_device_from_address(hass, mac, connectable=True)
    if device is None:
        raise CannotConnect(
            f"Wyze Lock Bolt {mac} is not visible to Home Assistant Bluetooth"
        )
    try:
        client = await establish_connection(BleakClient, device, device.address)
    except Exception as err:
        raise CannotConnect(f"Could not connect to Wyze Lock Bolt {mac}: {err}") from err

    try:
        state_raw = await client.read_gatt_char(STATE_UUID)
        try:
            decrypt_state_or_battery(state_key, bytes(state_raw))
        except Exception as err:
            raise InvalidKey(
                "State key does not decrypt the lock's state characteristic"
            ) from err

        battery_raw = await client.read_gatt_char(BATTERY_LEVEL_UUID)
        try:
            decrypt_state_or_battery(state_key, bytes(battery_raw))
        except Exception as err:
            raise InvalidKey(
                "State key does not decrypt the lock's battery characteristic"
            ) from err
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

#!/usr/bin/env python3
"""One-time cloud key extractor for Wyze Lock Bolt (local BLE operation).

Current Wyze Lock Bolt (YD_BT1) firmware requires two cloud-derived secrets
that are not derivable from the BLE MAC:
  - state_key    = uuid[-16:].lower()   (last 16 chars of the Wyze cloud
                                         device UUID; decrypts state blocks)
  - operate_key  = ble_token[16:]       (last 16 chars of the 32-hex BLE
                                         token; used for lock/unlock)
  - ble_id       = id from the same token response (BLE ID for config flow)

This drives wyzeapy (the library ha-wyzeapi itself uses), which fetches the
CBC-encrypted BLE token from Wyze and decrypts it with the known app secret.
Run once, paste the three values into the HA config flow, then the
integration runs fully local -- no further cloud contact.

Prerequisites:
  pip install wyzeapy

Get an API key + key id at https://developer.wyze.com

Usage:
  python3 wyze_key_extract.py
"""

from __future__ import annotations

import asyncio
import getpass
import sys


async def main() -> int:
    try:
        from wyzeapy import Wyzeapy
        from wyzeapy.const import FORD_APP_SECRET
        from wyzeapy.utils import wyze_decrypt_cbc
    except ImportError:
        print("ERROR: wyzeapy is not installed.")
        print("  pip install wyzeapy")
        return 1

    email = input("Wyze account email: ").strip()
    password = getpass.getpass("Wyze account password (hidden): ").strip()
    key_id = input("Key ID  (developer.wyze.com): ").strip()
    api_key = input("API key (developer.wyze.com): ").strip()

    print("\nLogging in...")
    wyze = Wyzeapy()
    try:
        await wyze.login(email, password, key_id, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"Login failed: {exc}")
        print("(If this mentions 2FA, run again after disabling 2FA or use"
              " the ha-wyzeapi debug-log method instead.)")
        return 1

    lock_service = await wyze.lock_service
    try:
        locks = await lock_service.get_locks()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to list locks: {exc}")
        return 1

    if not locks:
        print("No locks found on this account.")
        return 1

    any_missing = False
    for lock in locks:
        mac = str(lock.mac)
        name = str(getattr(lock, "nickname", "?"))
        print(f"\nLock: {name}")
        print(f"  UUID : {mac}")
        if len(mac) >= 16:
            print(f"  state_key   = {mac[-16:].lower()}")
        else:
            print("  state_key   = <uuid too short?>")
            any_missing = True

        try:
            info = await lock_service._get_lock_ble_token(lock)
            tok = info.get("token") or {}
            ble_id = tok.get("id")
            enc = tok.get("token")
            ble_token = wyze_decrypt_cbc(FORD_APP_SECRET[:16], enc)
            ble_token = str(ble_token).strip()
            if ble_id is not None:
                print(f"  ble_id      = {ble_id}   (BLE ID field in config)")
            if len(ble_token) >= 32:
                print(f"  operate_key = {ble_token[16:]}")
            else:
                print(f"  operate_key = <decrypted token too short: {ble_token!r}>")
                any_missing = True
        except Exception as exc:  # noqa: BLE001
            print(f"  operate_key = <FAILED: {exc}>")
            any_missing = True

    print(
        "\nPaste into the HA config flow:\n"
        "  state_key   -> 'State decryption key'  (16 chars)\n"
        "  operate_key -> 'Lock/unlock key'         (16 chars)\n"
        "  ble_id      -> 'BLE ID'                  (integer, if shown above)"
    )
    if any_missing:
        print(
            "\nFallback for operate_key: enable the official ha-wyzeapi\n"
            "integration with debug logging and grep home-assistant.log\n"
            "for 'ble_token' -> operate_key = its last 16 chars."
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

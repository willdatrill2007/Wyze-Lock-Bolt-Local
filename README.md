<p align="left">
  <a href="https://www.buymeacoffee.com/willdatrill2007" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" 
         alt="Buy Me A Coffee" 
         height="50" 
         width="210">
  </a>
</p>

# Wyze Lock Bolt Local

Local-only, cloud-free Home Assistant integration for the **Wyze Lock Bolt** (YD_BT1)
over Bluetooth Low Energy. No Wyze account, no app, no cloud — your lock, your keys,
your network.

## Features

- **Lock entity** with real-time state updates pushed by the lock itself
  (manual thumb turns show up in under a second — no polling delay)
- **Battery** sensor
- **Bluetooth signal strength (RSSI)** sensor — free, from HA's advertisement data
- **"Refresh now"** button entity
- **Bluetooth auto-discovery** — HA offers to set the lock up when it sees its BLE
  advertisement, MAC pre-filled
- **Setup-time key validation** — the config flow connects and decrypts live data
  before finishing, so a typo'd key fails immediately with a clear error
- **Automatic setup retry** — if the lock is out of range when HA boots, setup is
  retried automatically instead of leaving dead entities
- **Persistent connection mode with keepalive** (optional) — near-instant commands
- **Availability grace** — brief BLE hiccups don't flap entities unavailable
- **Diagnostics download** with keys automatically redacted
- Hardened against duplicate BLE notifications and flaky adapters

## Requirements

- Home Assistant 2024.1 or newer, with the **Bluetooth** integration working
  (a local USB/BLE adapter or an ESPHome Bluetooth proxy)
- A Wyze Lock Bolt (YD_BT1)
- Your lock's **local keys** (see below)

## Getting your lock's keys

The Bolt encrypts its state and commands with two local keys. You need:

| Value         | What it is                                                    |
| ------------- | ------------------------------------------------------------- |
| MAC           | The lock's BLE address (auto-filled on discovery)             |
| BLE ID        | Small integer ID (e.g. `1001`)                                |
| State key     | Last 16 characters of the lock's cloud device UUID            |
| Operate key   | Last 16 characters of the lock's 32-hex BLE token             |

### Extracting the keys (one-time, ~2 minutes)

Use the bundled helper `tools/wyze_key_extract.py`.
It contacts Wyze **once** to fetch the lock's secrets, prints the three values,
and after that the integration never talks to the cloud again:

```
pip install wyzeapy
python3 tools/wyze_key_extract.py
```

Prerequisites:

1. A free Wyze developer key: sign in at [developer.wyze.com](https://developer.wyze.com)
   and generate an **API key + Key ID**.
2. Your Wyze account credentials. If the account has 2FA enabled and login
   fails, either temporarily disable 2FA for the extraction or fall back to the
   ha-wyzeapi debug-log method described in the tool's output.

The tool prints `state_key`, `operate_key`, and `ble_id` — paste them into the
config flow fields of the same names. The config flow validates the state key
live during setup, so a wrong key is rejected on the spot with a clear error.

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/willdatrill2007/Wyze-Lock-Bolt-Local`, category **Integration**
3. Install **Wyze Lock Bolt Local** and restart Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=willdatrill2007&repository=Wyze-Lock-Bolt-Local&category=integration)

### Manual

Copy the `custom_components/wyze_bolt_local` folder into your `custom_components/`
directory and restart Home Assistant.

## Configuration

The lock is discovered automatically via Bluetooth (look for the discovered-device
notification, or add the integration manually and pick it from the list). If you
prefer manual setup, you only need the four values above.

## Options

| Option                  | Default    | Meaning                                                        |
| ----------------------- | ---------- | -------------------------------------------------------------- |
| Poll interval           | 30 s       | How often state/battery are read when not pushed               |
| Persistent connection   | off        | Keep the BLE link open for instant commands                    |
| Keepalive interval      | 0 (off)    | Periodic state reads that hold the link open (persistent only) |

### Choosing your settings (speed vs. battery)

Fast control requires the lock's radio to be awake, and this lock's firmware
fights hard to sleep — so there's a real trade-off. Measured behavior:

| Config                                   | Command speed                    | Battery                   | Notes                                                                                                                              |
| ---------------------------------------- | -------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Persistent **ON**, keepalive **2s**      | ~instant                         | worst                     | maximum responsiveness                                                                                                             |
| Persistent **ON**, keepalive **4s**      | ~instant, same as 2s             | marginally better         | Supervision timeout is 5s, so 4s is the slowest keepalive that still holds the link; the lock's own ~10s connection rotation causes the remaining brief dead windows |
| Persistent **ON**, keepalive **10s+**    | fast-ish                         | still high, more churn    | Supervision kills the link at ~5s of silence, so this degenerates into constant reconnect cycles — strictly worse than 4s or plain polling |
| Persistent **OFF**, poll **30s**         | 5–10s (lock asleep)              | good                      | the Wyze-app model: connect on demand                                                                                              |
| Persistent **OFF**, poll **60s**         | 5–10s                            | best                      | lock sleeps the most; manual turns up to 60s late                                                                                  |

**Recommended balance:** persistent ON + keepalive 4s + poll 60s. Keepalive 4s
holds the link just as well as 2s (the rotation cycles are the lock's firmware,
not something faster polling prevents), and poll 60s only gates the occasional
battery reconciliation read.

**If battery matters most:** persistent OFF + poll 60s, and accept 5–10s
commands — you're waking a sleeping lock.

**How to decide empirically:** watch the battery sensor — note the % today and
re-check in a couple of weeks at your chosen setting. The Bolt runs on AAs; if
you're replacing them more often than every few months, step down toward poll
mode. Also watch the RSSI sensor: a marginal link (−80 dBm or worse) causes
retries that cost more battery than any setting here, so placement is the
cheapest battery optimization of all.

### About persistent mode and battery life

This lock **drops idle Bluetooth connections after ~5 seconds** (its supervision
timeout), and it rotates long-lived connections every ~10–60 s regardless. The
integration handles all of this transparently — reconnecting in the background —
so the default (polling) mode works well for most people.

If you want sub-second commands, enable **persistent connection** and set a
**keepalive interval of 2–4 s**: periodic reads keep the supervision timer fed.
Trade-off: the lock's batteries drain noticeably faster. The lock also pushes its
state every couple of seconds while connected, so manual turns are instant either
way.

### A note on Bluetooth adapters

Some adapters (certain Realtek USB sticks in particular) deliver every BLE
notification twice. This integration deduplicates at the protocol layer and is
verified to work correctly on such hardware — but if you see duplicate log lines
or CRC errors, check for stale `__pycache__` folders and try another adapter first.

## Troubleshooting

- **"invalid state key" during setup** — the key doesn't decrypt the lock's data.
  Double-check the 16 characters from your dump.
- **"could not connect"** — the lock is out of range or asleep; try again standing
  next to it.
- **Entities unavailable after boot** — shouldn't happen; setup retries
  automatically. Check the log for `connection restored` lines.
- **Link drops every ~10 s in the log** — normal lock firmware behavior; the
  integration reconnects automatically.
- Download a **diagnostics report** from the integration page (keys are redacted
  automatically) and attach it to bug reports.
- Watch the **RSSI sensor**: consistently below about −80 dBm means range issues,
  not software issues.

## Protocol notes (for the curious)

- GATT UART service (`6e400001…`) carries a framed L1/L2 protocol (CRC-16/IBM)
- Lock state and battery are AES-ECB encrypted; the plaintext carries a `loock`
  validation marker — hence the lock's custom service UUID spelling `cool.knob`
- Lock/unlock commands use a challenge-response exchange signed with the operate key

## Support

If this integration saved you a hub subscription or a headache, consider
[buying me a coffee](https://github.com/willdatrill2007). Completely
optional — issues and PRs are always free.

## Credits

- Protocol reverse engineering by the wyze-lock-bolt-local contributors
- Built on [bleak](https://github.com/hbldh/bleak),
  [bleak-retry-connector](https://github.com/bluetooth-devices/bleak-retry-connector)
  and Home Assistant's Bluetooth integration

## Disclaimer

Not affiliated with or endorsed by Wyze Labs. Use at your own risk. Extracting
your lock's keys requires access to your own device and its data.

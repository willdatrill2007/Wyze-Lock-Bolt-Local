# Wyze Lock Bolt Local

Local-only, cloud-free Home Assistant integration for the **Wyze Lock Bolt** (YD_BT1)
over Bluetooth Low Energy. No Wyze account, no app, no cloud — your lock, your keys,
your network.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

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

| Value    | What it is                                        |
|----------|---------------------------------------------------|
| MAC      | The lock's BLE address (auto-filled on discovery) |
| BLE ID   | Small integer ID (e.g. `1001`)                    |
| State key| 16 ASCII characters                                |
| Operate key | 16 ASCII characters                             |

Both keys come from your **lock dump**. The state key decrypts everything the lock
broadcasts; the operate key signs lock/unlock commands.

> **TODO (maintainer):** document the exact dump procedure you used here — e.g.
> Android HCI snoop capture of the Wyze app while it operates the lock, or the
> extraction tool used. Point to the community write-up if one exists.

The config flow validates the state key live during setup, so a wrong key is
rejected on the spot with an "invalid state key" error.

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/YOUR_GITHUB_USERNAME/wyze-lock-bolt-local`, category **Integration**
3. Install **Wyze Lock Bolt Local** and restart Home Assistant

### Manual

Copy the `custom_components/wyze_bolt_local` folder into your `custom_components/`
directory and restart Home Assistant.

## Configuration

The lock is discovered automatically via Bluetooth (look for the discovered-device
notification, or add the integration manually and pick it from the list). If you
prefer manual setup, you only need the four values above.

## Options

| Option               | Default | Meaning                                                        |
|----------------------|---------|----------------------------------------------------------------|
| Poll interval        | 30 s    | How often state/battery are read when not pushed               |
| Persistent connection| off     | Keep the BLE link open for instant commands                    |
| Keepalive interval   | 0 (off) | Periodic state reads that hold the link open (persistent only) |

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

## Credits

- Protocol reverse engineering by the wyze-lock-bolt-local contributors
- Built on [bleak](https://github.com/hbldh/bleak),
  [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)
  and Home Assistant's Bluetooth integration

## Disclaimer

Not affiliated with or endorsed by Wyze Labs. Use at your own risk. Extracting
your lock's keys requires access to your own device and its data.

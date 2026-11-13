# Sundance RS485 → MQTT bridge

A Python bridge that joins a Sundance spa's RS485 panel bus over a TCP serial gateway and
publishes the spa to Home Assistant via MQTT discovery. It runs as one Docker container next
to my Home Assistant and gives me local control of my Sundance 780 with no cellular (ProLink)
or Wi‑Fi (SmartTub) module and no cloud.

```mermaid
flowchart LR
    SPA["Spa mainboard<br/>(Sundance 780)"]
    EW11["RS485 → TCP gateway<br/>(Elfin EW11, raw TCP :8899)"]
    BRIDGE["this bridge<br/>(Docker, pybalboa/app.py)"]
    MQTT["MQTT broker<br/>(Mosquitto)"]
    HA["Home Assistant<br/>(MQTT discovery)"]
    SPA <-- "RS485 bus,<br/>panel protocol" --> EW11
    EW11 <-- TCP --> BRIDGE
    BRIDGE <-- MQTT --> MQTT
    MQTT <--> HA
```

## What it does

The spa's topside panel talks to the mainboard over a multi-drop RS485 bus using a
Balboa-derived, XOR-obfuscated ("encrypted") protocol. The bridge:

1. **Connects** to the bus through the RS485-to-TCP gateway.
2. **Joins the bus as a virtual panel** — it performs the Balboa channel-assignment handshake,
   so the mainboard treats it like another control panel.
3. **Decodes** the status traffic: current and set temperature, heater state, pumps,
   circulation (off / auto / manual), lights and colours, clock, date, heat mode, the panel
   screen, the filter / UV / water-change service days, a second temperature sensor.
4. **Publishes** that state over MQTT using Home Assistant discovery, so the entities appear
   with no YAML.
5. **Accepts commands** back over MQTT (set point, pumps, circulation button, lights) and
   applies them by emulating panel button presses on the bus, with retry and confirmation.
   The **spa clock and date** are set the same way, by walking the panel's menus screen by
   screen and checking each step against what the panel reports (`pybalboa/panel_menu.py`).
6. **Stays connected** — a read timeout detects a stalled or half-open link, the availability
   topic flips to `offline`, and the bridge reconnects and re-negotiates its channel; the listen
   loop is supervised so an unexpected error restarts it rather than killing the process. On the
   MQTT side every connect, including paho's automatic reconnects, re-subscribes to the command
   topics, so a broker restart cannot leave the bridge publishing state while deaf to commands.

### Home Assistant entities

Created by discovery under the `homeassistant/` prefix, device **hottub**:

| Entity | Type | Notes |
| --- | --- | --- |
| Temperature | `climate` | Current temp, set temp, heat action; °F, 60–104 °F |
| Pump1, Pump2 | `switch` | Jet pumps |
| ManualCircPump | `switch` | The ClearRay / manual circulation button. Starts a manual run from off, stops one from manual. While circulation runs for heat, a filter cycle or the jets, the spa ignores the button, so the bridge refuses the command (`DECODING.md` §3.1) |
| Circulation Locked | `binary_sensor` | On while circulation runs automatically and the manual switch cannot change it |
| Last Command | `sensor` | The last command the bridge refused or stopped, and why. A set point stops after 64 unanswered presses, a pump or ClearRay after 10 |
| CirculationPump | `binary_sensor` | Circulation pump running (either mode) |
| Lights | `light` | On/off, brightness 0–100, effect = colour mode |
| Spa clock, spa date, heat mode, second temperature, circulation mode, auto / manual circulation, light cycle time, panel screen | `sensor` / `binary_sensor` | Read-only |
| Set Spa Time, Set Spa Date | `text` | Show the spa's clock / date; type `HH:MM` or `YYYY-MM-DD` to set it through the panel menus. Any time works, including across noon and midnight (`DECODING.md` §4; not yet tried on the live spa) |
| Sync Spa Clock | `button` | Sets the spa's date and time to now in `SPA_TZ` |
| Filter Change Days, UV Change Days, Water Change Days | `number` | The service timers; set them here (FC and UV move in 10-day steps, H2O in 30 — a value between steps lands on the nearest) |
| Panel Menu Status | `sensor` | Diagnostic: queued / running / done, read-back, or why a request was refused |
| Raw undecoded protocol fields | `sensor` | Diagnostic, disabled by default — for answering long-baseline questions from the recorder |

Light effects: Off, White, Blue, Violet, Red, Amber, Green, Aqua, Slow Blend, Fast Blend,
Frozen Blend. Heat modes decoded: AUTO, ECO, DAY. The heat mode is read-only: its panel edit
has not been recorded yet, so the bridge does not change it (`DECODING.md` §3.11 *Heat mode*).

**Only Sundance tubs.** Jacuzzi tubs emit a different, plaintext message family (`0x16` /
`0x17` / `0x23`); run through the Sundance XOR decoder they produce plausible nonsense, so they
raise an "Unknown Message" warning instead. The evidence is in
[`DECODING.md`](DECODING.md) §3.9 for anyone who wants to write a real Jacuzzi parser.

## The hardware it runs on

- **Spa:** Sundance 780 series. Other Sundance models on the same panel bus are likely to work
  but are untested.
- **Gateway:** an **Elfin EW11** RS485-to-Wi‑Fi module tapped onto the spa's panel RS485 pair,
  configured as a raw TCP server on port 8899 at the panel's baud settings. A USR-TCP232 or
  similar should do the same job.
- **Host:** a Raspberry Pi 4 running Docker, on the same LAN as the gateway and the Mosquitto
  broker that Home Assistant uses.

![The top edge of a Sundance 780 control board (Jacuzzi Products Co. PN 24168_C): water and
hi-limit temperature sensors at the left, the three MAIN PANEL sockets, then Zone 2 and Zone
1](img/780-board-top.jpg)

![Close-up of the three 4-pin MAIN PANEL sockets (J13, J14, J15), with the topside panel
cable in J13](img/780-main-panel-connectors.jpg)

On the 780 board (marked Jacuzzi Products Co. PN 24168_C) the panel bus has three 4-pin
(2×2) sockets marked **MAIN PANEL**: J13, J14 and J15, along the top edge. The topside
panel's cable uses one of them. The pin order is not recorded here, so
find A, B and ground on your own board before you connect anything.

> **Wiring into the RS485 bus is at your own risk.** You are joining the same bus the factory
> panel uses. Check polarity, and that the gateway is set to RS485, not RS232. See the
> disclaimer at the end.

## How to run it

All settings are environment variables; the defaults are placeholders.

| Variable | Meaning | Default |
| --- | --- | --- |
| `SERIAL_IP` | Address of the RS485→TCP gateway | `spa-bridge.local` |
| `SERIAL_PORT` | TCP port of the gateway | `8899` |
| `MQTT_HOST` | MQTT broker | `mqtt.local` |
| `MQTT_PORT` | MQTT port | `8883` with TLS, else `1883` |
| `MQTT_TLS` | `1` to connect over TLS, verifying the broker's certificate — so `MQTT_HOST` must be the name on it | unset |
| `MQTT_CA_FILE` | CA bundle for a privately-issued broker certificate; unset uses the system trust store | unset |
| `MQTT_USER` / `MQTT_PASSWORD` | Broker credentials; leave unset for an anonymous broker | unset |
| `SPA_TZ` | IANA time zone for **Sync Spa Clock**, e.g. `Europe/London` (the container clock is UTC) | unset — the button refuses |
| `RAW_FRAME_LOG` | Path to record every bus frame's raw bytes as JSON lines (rotating, 5 × 20 MB; `RAW_FRAME_LOG_BYTES` / `_FILES`). For decoding work — mount a volume for it | unset — off |

Docker:

```bash
docker build -t spa-rs485-mqtt .
docker run -d --name hottub --restart unless-stopped \
  -e SERIAL_IP=spa-bridge.local -e MQTT_HOST=mqtt.local \
  -e MQTT_USER=spa -e MQTT_PASSWORD=… \
  spa-rs485-mqtt
```

Without Docker:

```bash
pip install -r requirements.txt
cd pybalboa
SERIAL_IP=spa-bridge.local MQTT_HOST=mqtt.local python -u app.py
```

On start the log shows the channel handshake, then a decoded status line each time an
unknown field changes. Within a minute the **hottub** device appears in Home Assistant under
MQTT. `docker logs hottub` is also the input for the analysis tools in `tools/`.

Offline parser check, no spa needed: `python3 tools/test_decode.py`. Commands surviving a broker
restart, no broker needed: `python3 tools/test_resubscribe.py` (with `paho-mqtt<2.0` installed).
A new command dropping presses still queued for the old one: `python3 tools/test_commands.py`.
The menu driver against a simulated panel (dropped presses, either hour encoding, a person
pressing MENU mid-job): `python3 tools/test_menu.py`.

## Layout

| Path | What |
| --- | --- |
| `pybalboa/app.py` | The bridge: MQTT discovery, state publishing, command handling |
| `pybalboa/sundanceRS485.py` | `SundanceRS485` — channel negotiation, frame decoding, button emulation |
| `pybalboa/panel_menu.py` | Setting the clock, date and service timers by walking the panel menus — closed-loop, no I/O; the heat-mode refusal |
| `pybalboa/balboa.py` | Upstream `BalboaSpaWifi` base class |
| `pybalboa/__main__.py`, `pybalboa/testSundanceRS485.py` | Console read-outs for poking at a live tub |
| `DECODING.md` | The protocol record: every field, its confidence, the evidence, open questions |
| `tools/` | Log analysis scripts and the offline decode test (`tools/README.md`) |
| `Dockerfile`, `requirements.txt` | `python:alpine` + `paho-mqtt` |

## Where the code came from

This began as a fork of [garbled1/pybalboa](https://github.com/garbled1/pybalboa), which
speaks to Balboa's Wi‑Fi module over TCP. That code survives as `balboa.py` and the framing,
checksum and connection handling. Everything Sundance-specific is added on top in
`sundanceRS485.py`: the RS485 channel-assignment handshake that lets a third device join the
panel bus, the XOR fold and field map for the `0xC4` status and `0xCA` light frames, and
control by emulated panel button presses (`0xCC`) because Balboa's settings messages are not
accepted on this bus. `app.py` is new: pybalboa is a library, this is an MQTT service for
Home Assistant. The decoding is documented in `DECODING.md` rather than in the code alone.

This is Apache 2.0 code, and upstream's attribution below stays as long as `balboa.py` ships
here.

### Credits

Built on top of [garbled1/pybalboa](https://github.com/garbled1/pybalboa) (Tim Rightnour,
Nathan Spencer) — the `BalboaSpaWifi` base class — extended here with the `SundanceRS485`
subclass that implements the RS485 channel negotiation and the Sundance message decoding.

## Related projects

Other projects speak the same family of RS485 protocols. Pick by what your spa and your host are.

- [garbled1/pybalboa](https://github.com/garbled1/pybalboa) (Apache-2.0): the Python library for Balboa spas. Home Assistant's own `balboa` integration uses it. This repo's Sundance decode is proposed as a Sundance client for it in [issue #122](https://github.com/garbled1/pybalboa/issues/122). Use it if your spa has a Balboa Wi-Fi module.
- [Iraithium/sundance-rs485-controller](https://github.com/Iraithium/sundance-rs485-controller): an ESPHome component for Sundance spas on a plain ESP32, built on this decode. Home Assistant finds it with no MQTT bridge. The repository has no licence file, so this README links to it and does not reuse it.
- [dhmsjs/pyjacuzzi](https://github.com/dhmsjs/pyjacuzzi) (Apache-2.0): a Python decoder for Jacuzzi spas that extends pybalboa. This repo is Sundance-only, so Jacuzzi owners should start here.
- [jackbrown1993/Jacuzzi-RS485](https://github.com/jackbrown1993/Jacuzzi-RS485): an RS485 bridge for the Jacuzzi J335. Jacuzzi owners should read it.
- [ccutrer/balboa_worldwide_app](https://github.com/ccutrer/balboa_worldwide_app): a Ruby bridge that publishes Balboa spas over MQTT. It has no licence file. Use it if you prefer Ruby and have a standard Balboa board.
- [brianfeucht/esphome-balboa-spa](https://github.com/brianfeucht/esphome-balboa-spa) (BSD-2-Clause): an ESPHome component for Balboa spas, written in C++. Use it if you want an ESP device instead of a container.

pybalboa and the Sundance base class credited above are the code origin of this repo, so they are not repeated as credits here.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).

## Disclaimer

This is an unofficial project and is not affiliated with Balboa Water Group, Sundance or
Jacuzzi. Modifying or wiring into a spa's control bus may void warranties and carries risk of
damage or injury. Use entirely at your own risk.

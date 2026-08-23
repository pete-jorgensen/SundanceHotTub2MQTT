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

![The bridge's device page in Home Assistant — the climate control, switches and sensors,
all created by MQTT discovery](img/home-assistant.png)


The spa's topside panel talks to the mainboard over a multi-drop RS485 bus using a
Balboa-derived, XOR-obfuscated ("encrypted") protocol. The bridge:

1. **Connects** to the bus through the RS485-to-TCP gateway.
2. **Joins the bus as a virtual panel** — it performs the Balboa channel-assignment handshake,
   so the mainboard treats it like another control panel.
3. **Decodes** the status traffic: current and set temperature, heater state, pumps,
   circulation (off / auto / manual), lights and colours, clock, date, heat mode, the panel
   alert and its day count, a second temperature sensor.
4. **Publishes** that state over MQTT using Home Assistant discovery, so the entities appear
   with no YAML.
5. **Accepts commands** back over MQTT (set point, pumps, circulation button, lights) and
   applies them by emulating panel button presses on the bus, with retry and confirmation.
6. **Stays connected** — a read timeout detects a stalled or half-open link, the availability
   topic flips to `offline`, and the bridge reconnects and re-negotiates its channel; the listen
   loop is supervised so an unexpected error restarts it rather than killing the process.

### Home Assistant entities

Created by discovery under the `homeassistant/` prefix, device **hottub**:

| Entity | Type | Notes |
| --- | --- | --- |
| Temperature | `climate` | Current temp, set temp, heat action; °F, 60–104 °F |
| Pump1, Pump2 | `switch` | Jet pumps |
| ManualCircPump | `switch` | The ClearRay / manual circulation button |
| CirculationPump | `binary_sensor` | Circulation pump running (either mode) |
| Lights | `light` | On/off, brightness 0–100, effect = colour mode |
| Spa clock, spa date, heat mode, second temperature, circulation mode, auto / manual circulation, panel alert + day count, light cycle time | `sensor` / `binary_sensor` | Read-only |
| Raw undecoded protocol fields | `sensor` | Diagnostic, disabled by default — for answering long-baseline questions from the recorder |

Light effects: Off, White, Blue, Violet, Red, Amber, Green, Aqua, Slow Blend, Fast Blend,
Frozen Blend. Heat modes decoded: AUTO, ECO, DAY.

**Only Sundance tubs.** Jacuzzi tubs emit a different, plaintext message family (`0x16` /
`0x17` / `0x23`); earlier versions routed those through the Sundance XOR decoder and produced
plausible nonsense. They now raise an "Unknown Message" warning instead. The evidence is in
[`DECODING.md`](DECODING.md) §3.9 for anyone who wants to write a real Jacuzzi parser.

## The hardware it runs on

- **Spa:** Sundance 780 series. Other Sundance models on the same panel bus are likely to work
  but are untested.
- **Gateway:** an **Elfin EW11** RS485-to-Wi‑Fi module tapped onto the spa's panel RS485 pair,
  configured as a raw TCP server on port 8899 at the panel's baud settings. A USR-TCP232 or
  similar should do the same job.
- **Host:** a Raspberry Pi 4 running Docker, on the same LAN as the gateway and the Mosquitto
  broker that Home Assistant uses.

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
| `MQTT_PORT` | MQTT port | `1883` |
| `MQTT_USER` / `MQTT_PASSWORD` | Broker credentials; leave unset for an anonymous broker | unset |

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

Offline parser check, no spa needed: `python3 tools/test_decode.py`.

## Layout

| Path | What |
| --- | --- |
| `pybalboa/app.py` | The bridge: MQTT discovery, state publishing, command handling |
| `pybalboa/sundanceRS485.py` | `SundanceRS485` — channel negotiation, frame decoding, button emulation |
| `pybalboa/balboa.py` | Upstream `BalboaSpaWifi` base class |
| `pybalboa/__main__.py`, `pybalboa/testSundanceRS485.py` | Console read-outs for poking at a live tub |
| `DECODING.md` | The protocol record: every field, its confidence, the evidence, open questions |
| `tools/` | Log analysis scripts and the offline decode test (`tools/README.md`) |
| `Dockerfile`, `requirements.txt` | `python:alpine` + `paho-mqtt` |

## Relation to upstream

This is a fork of [garbled1/pybalboa](https://github.com/garbled1/pybalboa), which speaks to
Balboa's Wi‑Fi module over TCP. That code survives as `balboa.py` and the framing, checksum
and connection handling. Everything Sundance-specific is added on top in `sundanceRS485.py`:
the RS485 channel-assignment handshake that lets a third device join the panel bus, the XOR
fold and field map for the `0xC4` status and `0xCA` light frames, and control by emulated
panel button presses (`0xCC`) because the upstream settings messages are not accepted on this
bus. `app.py` is new: upstream is a library, this is an MQTT service for Home Assistant. The
decoding is documented in `DECODING.md` rather than in the code alone. The `main` branch
mirrors upstream; `master` is this project.

### Credits

Built on top of [garbled1/pybalboa](https://github.com/garbled1/pybalboa) (Tim Rightnour,
Nathan Spencer) — the `BalboaSpaWifi` base class — extended here with the `SundanceRS485`
subclass that implements the RS485 channel negotiation and the Sundance message decoding.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).

## Disclaimer

This is an unofficial project and is not affiliated with Balboa Water Group, Sundance or
Jacuzzi. Modifying or wiring into a spa's control bus may void warranties and carries risk of
damage or injury. Use entirely at your own risk.

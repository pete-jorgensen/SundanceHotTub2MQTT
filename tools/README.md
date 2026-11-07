# Decode analysis tools

Offline tools for working on the protocol decode. None of them need the spa, a socket or
MQTT, except `test_resubscribe.py`, which brings its own broker.

## Log analysis

`analyse_c4.py`, `correlate.py`, `deep_analysis.py` and `deep_analysis2.py` read the
bridge's own log format — lines containing `Full Message: C4: [...]` — so they work
directly against the container log. Feed them a captured log:

```sh
docker logs --since 48h hottub 2>&1 | grep "Full Message: C4" > frames.txt
python3 tools/analyse_c4.py frames.txt    # per-field value distribution + per-bit constancy
python3 tools/correlate.py  frames.txt    # cross-tabulate the flag bits against each other
```

**Read `DECODING.md` §0 before drawing conclusions.** Logs from older versions of the bridge
only carry a frame when an *unknown* field changed, so frames where only a known field moved
are missing there; the bridge now logs every changed frame. "This field is
constant" and "bit A tracks bit B" survive that bias; "this pump only ran twice" does not.

`analyse_c4.py` produced the field table in `DECODING.md` §2; `correlate.py` produced the
circulation-signal matrix in §3.1 and the display-vs-date table in §3.2. The two
`deep_analysis*.py` scripts look for structure the field map misses, with sample-size gating
(§3.10).

## Weekday check

`weekday_f2.py` decodes the spa's own date from every saved `C4` frame and compares `f2 & 7`
with that date's weekday (Sunday = 0) — the evidence for `DECODING.md` §6.3:

```sh
python3 tools/weekday_f2.py captures/*.txt
```

## Cross-model check

`cross_model_check.py` prints what the 780 column of `DECODING.md` §7 rests on: every panel
press with the pump or circulation bit it changed, pump and circulation edges against `f14`
bit 5, `f1` b5 / b6 / b7 transitions with their times, `(f5 ^ 2) − f14` with and without
bit 5 undone, and every `f2` bit 5 edge:

```sh
python3 tools/cross_model_check.py captures/*.txt
```

## Panel walk timeline

`panel_walk.py` interleaves the real panel's button presses with the frames they caused, showing
only the fields that moved — how the menus were decoded (`DECODING.md` §3.11):

```sh
python3 tools/panel_walk.py panel-walk.txt
```

## Menu driver check

`test_menu.py` runs `pybalboa/panel_menu.py` against a simulated panel that behaves as the real
one did on the walk, and the same job through `SundanceRS485` with synthetic frames.

## Parser regression check

`test_decode.py` builds synthetic raw frames that XOR-fold to known decoded arrays and runs
`parse_C4status_update` over them, asserting the decoded attributes:

```sh
python3 tools/test_decode.py
```

## MQTT reconnect check

`test_resubscribe.py` runs `app.py`'s real MQTT client against a minimal in-process broker,
restarts that broker under it, and asserts a set-point command still reaches the spa object.
It needs the bridge's own dependency, `paho-mqtt<2.0`:

```sh
python3 tools/test_resubscribe.py
```

## Command retargeting check

`test_commands.py` feeds synthetic status frames to the real parser and asserts that a new
target drops presses still queued for the old one: a set point reversed mid-move, and a pump
turned off before its ON press went out. No spa, socket or MQTT:

```sh
python3 tools/test_commands.py
```

## Gateway reconnect check

`test_reconnect.py` runs the real `listen()` loop against a fake EW11 on localhost (two client
slots, no idle timeout, accept-then-close when full). It asserts that a link the bridge gives
up on is closed before the next one opens, and that refused reconnects back off rather than
spin. No spa, LAN or MQTT:

```sh
python3 tools/test_reconnect.py
```

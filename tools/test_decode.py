#!/usr/bin/env python3
"""Offline check of parse_C4status_update against real captured frames.

Builds a synthetic raw frame that xor-folds to a known decoded array, so the parser
can be exercised with no spa, no socket and no MQTT. Run from the repo root:

    python3 tools/test_decode.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pybalboa"))
import sundanceRS485  # noqa: E402


def frame(fields):
    """Raw frame whose xormsg() output is `fields`. xormsg: c = d[2i]^d[2i+1]^1."""
    payload = bytearray()
    for v in fields:
        payload += bytes([0, v ^ 1])
    return bytes(5) + payload + bytes(2)


def check(name, fields, expected):
    spa = sundanceRS485.SundanceRS485("127.0.0.1")
    spa.log.setLevel(50)
    asyncio.run(spa.parse_C4status_update(frame(fields)))
    bad = []
    for attr, want in expected.items():
        got = getattr(spa, attr)
        if got != want:
            bad.append("  %-16s expected %-8r got %r" % (attr, want, got))
    print(("PASS  " if not bad else "FAIL  ") + name)
    for line in bad:
        print(line)
    return not bad


# Real frames from a three-day capture of the bridge log.
CASES = [
    ("auto circulation (f1=96 f4=69 f10=128, seen 411x)",
     [13, 96, 5, 250, 69, 99, 32, 137, 97, 14, 128, 39, 0, 132, 65, 0],
     dict(circ_pump_status=1, autoCirc=1, manualCirc=0, circMode="auto",
          alertActive=1, alertDayCount=4, curtemp=97.0, settemp=97.0,
          time_hour=11, time_minute=39, day=17, temp2Valid=97.0)),

    ("manual circ / ClearRay (f1=224 f4=69 f10=0, seen 2x)",
     [19, 224, 0, 250, 69, 99, 32, 153, 96, 14, 0, 34, 0, 130, 64, 0],
     dict(circ_pump_status=1, autoCirc=0, manualCirc=1, circMode="manual",
          alertActive=1, alertDayCount=2, temp2Valid=96.0)),

    ("manual circ, mid-transition (f1=160)",
     [19, 160, 0, 250, 69, 99, 32, 153, 96, 14, 0, 34, 0, 130, 64, 0],
     dict(autoCirc=0, manualCirc=1, circMode="manual", circB5=1, circB6=0)),

    ("everything off (f1=0 f4=5 f10=0, seen 1001x)",
     [21, 0, 0, 250, 5, 99, 32, 153, 96, 14, 0, 46, 0, 130, 96, 0],
     dict(circ_pump_status=0, autoCirc=0, manualCirc=0, circMode="off",
          heatstate=0, temp2Valid=96.0, heatModeText="AUTO")),

    ("heater on (f10 bit6)",
     [21, 96, 5, 250, 69, 99, 32, 153, 96, 14, 192, 46, 0, 130, 65, 0],
     dict(heatstate=1, autoCirc=1, circMode="auto")),

    ("f14 raw 193 is not an outlier - bit 7 is a flag; 193 & 0x7F = 65, +32 = 97 = temp1",
     [21, 96, 5, 250, 69, 99, 32, 153, 96, 14, 128, 46, 0, 130, 193, 0],
     dict(temp2Valid=97.0, temp2Flag=1)),

    ("a genuinely implausible f14 is still rejected",
     [21, 96, 5, 250, 69, 99, 32, 153, 96, 14, 128, 46, 0, 130, 5, 0],
     dict(temp2Valid=None)),

    ("non-alert display (bit 7 clear) keeps the 2023 map meaning",
     [21, 0, 0, 250, 5, 99, 32, 153, 96, 14, 0, 46, 0, 23, 96, 0],
     dict(alertActive=0, alertDayCount=7, displayTextS="Current Temp")),
]

if __name__ == "__main__":
    ok = all([check(n, f, e) for n, f, e in CASES])
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)

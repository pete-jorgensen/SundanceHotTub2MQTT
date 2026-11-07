#!/usr/bin/env python3
"""Offline check of parse_C4status_update against real captured frames.

Builds a synthetic raw frame that xor-folds to a known decoded array, so the parser
can be exercised with no spa, no socket and no MQTT. Run from the repo root:

    python3 tools/test_decode.py
"""
import asyncio
import os
import sys
from datetime import date

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
          curtemp=97.0, settemp=97.0,
          time_hour=11, time_minute=39, temp2Valid=97.0)),

    ("manual circ / ClearRay (f1=224 f4=69 f10=0, seen 2x)",
     [19, 224, 0, 250, 69, 99, 32, 153, 96, 14, 0, 34, 0, 130, 64, 0],
     dict(circ_pump_status=1, autoCirc=0, manualCirc=1, circMode="manual",
          temp2Valid=96.0)),

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

    ("an idle panel's display field is the UV days, and its state is HOME",
     [21, 0, 0, 250, 5, 99, 32, 153, 96, 14, 0, 46, 0, 56, 96, 0],
     dict(uvDays=56, panelState=0, displayTextS="HOME")),
    ("the three-day log's 'alert' values were the UV days counting down (132)",
     [13, 96, 5, 250, 69, 99, 32, 137, 97, 14, 128, 39, 0, 132, 65, 0],
     dict(uvDays=132, panelState=0)),
]



def check_sequence(name, frames, expected, today):
    """Feed several frames to one spa object, then check - for decodes that use history."""
    spa = sundanceRS485.SundanceRS485("127.0.0.1")
    spa.log.setLevel(50)
    spa.today = lambda: today
    for fields in frames:
        asyncio.run(spa.parse_C4status_update(frame(fields)))
    bad = ["  %-16s expected %-8r got %r" % (a, w, getattr(spa, a))
           for a, w in expected.items() if getattr(spa, a) != w]
    print(("PASS  " if not bad else "FAIL  ") + name)
    for line in bad:
        print(line)
    return not bad


def with_date(f7, f9):
    f = [21, 0, 0, 250, 5, 99, 32, 153, 96, 14, 0, 46, 0, 56, 96, 0]
    f[7], f[9] = f7, f9
    return f


# The date field is (day << 3) XOR month (DECODING.md 3.3). Frames from the panel
# walk and from the three-day log, whose spa still believed it was 2014.
SEQUENCES = [
    ("Oct 25 2026 as set on the panel (194, year field 26)",
     [with_date(194, 26)], dict(year=2026, month=10, day=25), date(2026, 10, 1)),
    ("Apr 3 2014, the date before it was set (28, 14), once a day change has been seen",
     [with_date(28, 14), with_date(36, 14)], dict(year=2014, month=4, day=4), date(2026, 10, 1)),
    ("137 alone is Jan 17 or Sep 16; the midnight to 145 settles it as January",
     [with_date(137, 14), with_date(145, 14)], dict(month=1, day=18), date(2026, 10, 1)),
    ("... and a third day keeps it",
     [with_date(137, 14), with_date(145, 14), with_date(153, 14)], dict(month=1, day=19),
     date(2026, 10, 1)),
    ("a month rollover is not mistaken for the other reading (Sep 30 -> Oct 1)",
     [with_date((30 << 3) ^ 9, 26), with_date((1 << 3) ^ 10, 26)], dict(month=10, day=1),
     date(2026, 10, 1)),
    ("the year field is the year - 2000", [with_date(194, 25)], dict(year=2025), date(2026, 10, 1)),
]

if __name__ == "__main__":
    ok = all([check(n, f, e) for n, f, e in CASES] +
             [check_sequence(n, f, e, d) for n, f, e, d in SEQUENCES])
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)

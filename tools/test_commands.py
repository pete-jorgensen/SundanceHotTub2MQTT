#!/usr/bin/env python3
"""Offline check that a new target never sends a press that served the old one.

Commands become emulated panel presses: queued on a status frame, sent on the bridge's next
clear-to-send. A new target can arrive in between, so the queued press must go, or the spa
moves the old way first (set 100, then 95 while at 99, went 99 -> 100 -> 95) - and for a
toggle button it turns on what was just turned off. Feeds synthetic status frames to the real
parser; no spa, no socket, no MQTT. Run from the repo root:

    python3 tools/test_commands.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.environ.get(
    "APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pybalboa")))
import sundanceRS485 as S  # noqa: E402

# "everything off" from test_decode.py: field 8 is the set point, field 2 bit 4 is pump 1
BASE = [21, 0, 0, 250, 5, 99, 32, 153, 96, 14, 0, 46, 0, 130, 96, 0]


def frame(fields):
    """Raw frame whose xormsg() output is `fields`. xormsg: c = d[2i]^d[2i+1]^1."""
    payload = bytearray()
    for v in fields:
        payload += bytes([0, v ^ 1])
    return bytes(5) + payload + bytes(2)


def new_spa():
    spa = S.SundanceRS485("127.0.0.1")
    spa.log.setLevel(50)
    spa.connected, spa.channel = True, 17      # send_CCmessage queues only with both
    return spa


def feed(spa, settemp, pump1_on=0):
    fields = list(BASE)
    fields[8] = settemp
    fields[2] = 1 << 4 if pump1_on else 0
    asyncio.run(spa.parse_C4status_update(frame(fields)))


def queued(spa):
    return [msg[5] for msg in spa.queue.queue]


def run(coro):
    asyncio.run(coro)


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "   got %r" % (detail,)))


# 1. the measured case: up to 100 in progress, 95 arrives while the spa reads 99
spa = new_spa()
feed(spa, 99)
run(spa.send_temp_change(100))
feed(spa, 99)
check("an UP press is queued on the way to 100", queued(spa) == [S.BTN_TEMP_UP], queued(spa))
run(spa.send_temp_change(95))
check("retargeting to 95 drops the UP that has not gone out", S.BTN_TEMP_UP not in queued(spa),
      queued(spa))
for _ in range(4):
    feed(spa, 99)
check("... and the next press is DOWN", queued(spa)[:1] == [S.BTN_TEMP_DOWN], queued(spa))

# 2. pump on, then off before the press went out
spa = new_spa()
feed(spa, 96, pump1_on=0)
run(spa.change_pump(0, 1))
feed(spa, 96, pump1_on=0)
check("a Pump 1 press is queued for ON", queued(spa) == [S.BTN_P1], queued(spa))
run(spa.change_pump(0, 0))
check("OFF before it went out drops that press", S.BTN_P1 not in queued(spa), queued(spa))
for _ in range(4):
    feed(spa, 96, pump1_on=0)
check("... and nothing presses Pump 1 afterwards", S.BTN_P1 not in queued(spa), queued(spa))
check("... and no target is left pending",
      spa.target_pump_status[0] == S.NO_CHANGE_REQUESTED, spa.target_pump_status[0])

# 3. a retarget drops only its own control's presses
spa = new_spa()
run(spa.send_CCmessage(S.BTN_TEMP_UP))
run(spa.send_CCmessage(S.BTN_P1))
run(spa.change_pump(0, 0))
check("retargeting a pump leaves a queued temperature press alone",
      queued(spa) == [S.BTN_TEMP_UP], queued(spa))

if __name__ == "__main__":
    ok = all(results)
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)

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


# Circulation fields f1, f4, f10 from the DECODING.md 3.1 truth table.
CIRC = {"off": (0, 5, 0), "auto": (96, 69, 128), "manual": (224, 69, 0)}


def feed(spa, settemp, pump1_on=0, circ="off"):
    fields = list(BASE)
    fields[8] = settemp
    fields[2] = 1 << 4 if pump1_on else 0
    fields[1], fields[4], fields[10] = CIRC[circ]
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

# 4. circulation's three states (DECODING.md 3.1): off -> manual, manual -> off, auto locked
C = S.CIRC_PUMP
spa = new_spa()
feed(spa, 102, circ="auto")
check("auto-running reads as mode auto, locked", (spa.circMode, spa.circLocked) == ("auto", 1),
      (spa.circMode, spa.circLocked))
run(spa.change_pump(C, 1))
for _ in range(6):
    feed(spa, 102, circ="auto")
check("ON while auto-running presses nothing", S.BTN_CLEAR_RAY not in queued(spa), queued(spa))
check("... leaves no target pending", spa.target_pump_status[C] == S.NO_CHANGE_REQUESTED,
      spa.target_pump_status[C])
check("... and says why", spa.commandStatus.startswith("refused:"), spa.commandStatus)

spa = new_spa()
feed(spa, 102, circ="off")
check("off reads as mode off, unlocked", (spa.circMode, spa.circLocked) == ("off", 0),
      (spa.circMode, spa.circLocked))
run(spa.change_pump(C, 1))
feed(spa, 102, circ="off")
check("ON while off presses ClearRay", queued(spa) == [S.BTN_CLEAR_RAY], queued(spa))
spa.queue.queue.clear()
for _ in range(4):
    feed(spa, 102, circ="manual")
check("... and stops once manual circulation shows",
      spa.target_pump_status[C] == S.NO_CHANGE_REQUESTED and not queued(spa),
      (spa.target_pump_status[C], queued(spa)))

spa = new_spa()
feed(spa, 102, circ="manual")
run(spa.change_pump(C, 0))
feed(spa, 102, circ="manual")
check("OFF while manual presses ClearRay", queued(spa) == [S.BTN_CLEAR_RAY], queued(spa))

spa = new_spa()
feed(spa, 102, circ="off")
run(spa.change_pump(C, 1))
feed(spa, 102, circ="auto")
check("a pending ON is dropped when heat takes circulation over",
      spa.target_pump_status[C] == S.NO_CHANGE_REQUESTED and not queued(spa),
      (spa.target_pump_status[C], queued(spa)))

# 5. a spa that never answers: the press stops at the cap, and the target is dropped
spa = new_spa()
feed(spa, 96, circ="off")
run(spa.change_pump(0, 1))
for _ in range(200):
    feed(spa, 96, pump1_on=0, circ="off")
presses = [b for b in queued(spa) if b == S.BTN_P1]
check("an unanswered pump stops at MAX_TOGGLE_PRESSES", len(presses) == S.MAX_TOGGLE_PRESSES,
      len(presses))
check("... and its target is dropped", spa.target_pump_status[0] == S.NO_CHANGE_REQUESTED,
      spa.target_pump_status[0])

spa = new_spa()
feed(spa, 96)
run(spa.send_temp_change(104))
for _ in range(400):
    feed(spa, 96)
ups = [b for b in queued(spa) if b == S.BTN_TEMP_UP]
check("an unanswered set point stops at MAX_TEMP_PRESSES", len(ups) == S.MAX_TEMP_PRESSES, len(ups))
check("... and its target is dropped", spa.targetTemp == S.NO_CHANGE_REQUESTED, spa.targetTemp)

if __name__ == "__main__":
    ok = all(results)
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)

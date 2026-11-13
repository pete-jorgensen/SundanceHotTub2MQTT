#!/usr/bin/env python3
"""Offline check of the panel-menu driver (pybalboa/panel_menu.py) against a simulated panel.

The simulated panel behaves as the real one did on the panel walk (DECODING.md 3.11):
MENU steps through the eleven screens and wraps, UP opens an edit, UP/DOWN step the value shown
in f15, Pump 1 accepts a field and moves on, and the last accept saves and returns to the screen.
It can drop presses. Opening DATE / TIME takes two UPs, and the second one steps the first
field, as on the real panel. No spa, socket or MQTT. Run from the repo root:

    python3 tools/test_menu.py
"""
import asyncio
import os
import random
import sys
from datetime import date

sys.path.insert(0, os.environ.get(
    "APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pybalboa")))
import panel_menu as M  # noqa: E402
import sundanceRS485 as S  # noqa: E402

ORDER = [M.HOME] + [M.SCREEN[n] for n in M.MENU_ORDER]
GRID = {"FC": 10, "UV": 10, "H2O": 30}     # FC 20 -> 30 -> 40, UV 56 -> 60, H2O 30 -> 60


class Panel:
    """The panel as observed: `screen` is the STATE; the wire carries state ^ uv (f13)."""
    def __init__(self, ymd=(2026, 10, 25), hm=(18, 37), service=(40, 60, 60), drop=0.0, seed=1):
        self.y, self.mo, self.d = ymd
        self.h, self.mi = hm
        self.fc, self.uv, self.h2o = service
        self.screen, self.edit = M.HOME, 0
        self.hold = self.mi         # the minute held by an open TIME edit (the hour field's key)
        self.fields = None          # the open edit: [state, value] pairs being changed
        self.pos = 0
        self.origin = None          # the menu screen the edit was opened from
        self.opened = None          # the menu name while in its "unchanged" state
        self.rng = random.Random(seed)
        self.drop = drop
        self.presses = []

    @property
    def raw(self):
        return self.screen ^ self.uv

    def shown(self):
        """f15 for the open edit field."""
        code, v = self.fields[self.pos]
        if code == M.TIME_EDIT[0]:
            return v ^ self.hold                       # the hour (12 is 0), keyed by the minute
            #                                            held when the edit OPENED, not the live one
        if code == M.TIME_EDIT[1]:
            return v ^ self.fields[0][1]               # the minute, keyed by the hour set (12 is 0)
        return v                                       # AM/PM: 1 = PM, 0 = AM, independent of the hour

    def tick(self):
        """The running clock gains a minute (and rolls the hour), as it does mid-edit."""
        self.mi += 1
        if self.mi == 60:
            self.mi, self.h = 0, (self.h + 1) % 24

    def _service(self, name):
        return {"FC": self.fc, "UV": self.uv, "H2O": self.h2o}[name]

    def press(self, button):
        self.presses.append(button)
        if self.rng.random() < self.drop:
            return
        names = {v: k for k, v in M.SCREEN.items()}
        if self.opened is not None:                    # an "unchanged" first field
            name = self.opened
            if button == M.BTN_UP:
                self.opened = None
                if name == "DATE":                     # the year does not step (live: 32 -> 33 at 26)
                    self.fields = [[M.DATE_EDIT[0], self.y - 2000], [M.DATE_EDIT[1], self.mo],
                                   [M.DATE_EDIT[2], self.d]]
                elif name == "TIME":                   # the hour does (live: 7 -> 8 PM; 11 -> 12)
                    self.fields = [[M.TIME_EDIT[0], (self.h + 1) % 12], [M.TIME_EDIT[1], self.mi],
                                   [M.TIME_EDIT[2], 1 if self.h >= 12 else 0]]
                else:
                    g = GRID[name]
                    v = self._service(name)
                    self.fields = [[M.SERVICE_EDIT[name], (v // g + 1) * g]]   # 56 -> 60
                self.pos = 0
            elif button == M.BTN_SELECT and name == "TIME":   # P1 pressed on screen 35 (hour unchanged) -> minute
                self.opened = None
                self.fields = [[M.TIME_EDIT[0], self.h % 12], [M.TIME_EDIT[1], self.mi],
                               [M.TIME_EDIT[2], 1 if self.h >= 12 else 0]]
                self.pos = 1
            self._show()
            return
        if self.fields is None:
            if button == M.BTN_MENU:
                self.screen = ORDER[(ORDER.index(self.screen) + 1) % len(ORDER)]
            elif button == M.BTN_UP and names.get(self.screen) in M.OPENED:
                self.origin = self.screen
                self.opened = names[self.screen]
                self.screen = M.OPENED[self.opened]
                self.hold = self.mi                    # the minute the TIME edit holds from now on
                self.edit = self._service(self.opened) if self.opened in GRID else \
                    ((self.h % 12) ^ self.mi if self.opened == "TIME" else 0)
                return
            self._show()
            return
        f = self.fields[self.pos]
        name = names.get(self.origin)
        if button in (M.BTN_UP, M.BTN_DOWN):
            step = GRID.get(name, 1)
            f[1] += step if button == M.BTN_UP else -step
            if name == "TIME" and f[0] == M.TIME_EDIT[0]:
                f[1] %= 12                              # the hour wraps 11 -> 0 (12) -> 1
            elif name == "TIME" and f[0] == M.TIME_EDIT[2]:
                f[1] = min(max(f[1], 0), 1)
            else:
                f[1] = max(f[1], 0)
        elif button == M.BTN_SELECT:
            self.pos += 1
            if self.pos == len(self.fields):
                vals = [v for _, v in self.fields]
                if name == "DATE":
                    self.y, self.mo, self.d = 2000 + vals[0], vals[1], vals[2]
                elif name == "TIME":
                    self.h, self.mi = vals[0] + 12 * vals[2], vals[1]   # hour 0 is 12 o'clock
                elif name == "FC":
                    self.fc = vals[0]
                elif name == "UV":
                    self.uv = vals[0]                  # ... and the display key with it
                else:
                    self.h2o = vals[0]
                self.fields = None
                self.screen = self.origin
        self._show()

    def _show(self):
        if self.fields is None:
            self.edit = 0
        else:
            self.screen, self.edit = self.fields[self.pos][0], self.shown()

    def timeout(self):
        self.screen, self.edit, self.fields, self.opened = M.HOME, 0, None, None


class Readback:
    """What the spa object would decode, for MenuJob.check / precheck."""
    def __init__(self, p):
        self.year, self.month, self.day = p.y, p.mo, p.d
        self.time_hour, self.time_minute = p.h, p.mi
        self.filterDays, self.uvDays, self.waterDays = p.fc, p.uv, p.h2o


def drive(job, panel, max_frames=3000, tick_at=None):
    """Run a job to completion as the spa would: one press per frame at most, taking effect a
    frame later; the state is decoded with the key the bridge knows, re-keyed when UV saves."""
    if job.precheck:
        why = job.precheck(Readback(panel))
        if why:
            job.fail(why)
            return job
    pending, t, key = None, 0.0, panel.uv
    for frame_no in range(max_frames):
        t += 0.5
        if tick_at is not None and frame_no == tick_at:
            panel.tick()
        if pending is not None:
            panel.press(pending)
            pending = None
        pending = job.on_frame(panel.raw ^ key, panel.edit, now=t,
                               ctx={"minute": panel.mi, "raw_screen": panel.raw})
        if job.new_uv is not None:
            key, job.new_uv = job.new_uv, None
        if job.finished:
            break
    if job.status.startswith("done") and job.check:
        why = job.check(Readback(panel))
        if why:
            job.status = "failed: " + why
    return job


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "   got %r" % (detail,)))


p = Panel(ymd=(2014, 4, 3))
j = drive(M.date_job(2026, 10, 25), p)
check("date: Apr 3 2014 -> Oct 25 2026", j.status.startswith("done") and (p.y, p.mo, p.d) == (2026, 10, 25),
      (j.status, p.y, p.mo, p.d))
check("... and it ends on the DATE screen, not in an edit", p.screen == M.SCREEN["DATE"] and p.fields is None,
      p.screen)

p = Panel(ymd=(2026, 10, 25), drop=0.2, seed=7)
j = drive(M.date_job(2026, 11, 1), p)
check("date: survives 20% of presses being dropped", j.status.startswith("done") and (p.mo, p.d) == (11, 1),
      (j.status, p.mo, p.d))

for start, target in (((14, 13), (18, 37)), ((19, 50), (18, 5)), ((1, 5), (10, 59)),
                      ((22, 0), (13, 30))):
    p = Panel(hm=start)
    j = drive(M.time_job(target[0], target[1], p.h), p)
    check("time: %02d:%02d -> %02d:%02d" % (start + target),
          j.status.startswith("done") and (p.h, p.mi) == target, (j.status, p.h, p.mi))

# Noon and midnight (DECODING.md 3.11): 12 o'clock is 0 in the hour field, AM/PM is its own field.
# 11:59 -> 12:00 and 23:59 -> 00:00 are the two crossings; a set across each; and then every
# start hour against every target hour, so no 11 -> 12 -> 1 wrap or AM/PM flip is left untried.
for start, target in (((11, 59), (12, 0)), ((23, 59), (0, 0)), ((11, 20), (12, 5)), ((23, 50), (0, 10)),
                      ((11, 30), (13, 0)), ((9, 0), (12, 0)), ((23, 0), (13, 0)), ((11, 5), (10, 0)),
                      ((12, 0), (11, 59)), ((0, 0), (23, 59)), ((12, 30), (0, 30)), ((0, 15), (12, 15))):
    p = Panel(hm=start)
    j = drive(M.time_job(target[0], target[1], p.h), p)
    check("time: %02d:%02d -> %02d:%02d across noon / midnight / 12 o'clock" % (start + target),
          j.status.startswith("done") and (p.h, p.mi) == target, (j.status, p.h, p.mi))

bad = []
for sh in range(24):
    for th in range(24):
        p = Panel(hm=(sh, 17))
        j = drive(M.time_job(th, 41, p.h), p)
        if not (j.status.startswith("done") and (p.h, p.mi) == (th, 41)):
            bad.append((sh, th, j.status, p.h, p.mi))
check("time: all 24 x 24 start/target hours set exactly (%d failed)" % len(bad), not bad, bad[:3])

# The clock ticks while the edit is open (it does: the walk's 25 -> 26 mid-edit). The hour field
# keeps its key from the minute the edit opened, so the job must not follow the live minute -
# including a tick that rolls the hour over (11:59 -> 12:00, 23:59 -> 00:00).
bad = []
for start in ((11, 59), (23, 59), (6, 25), (12, 59), (0, 59)):
    for target in ((12, 0), (0, 0), (13, 5), (1, 5), (6, 30), (18, 25)):
        for tick_at in range(0, 70, 3):
            p = Panel(hm=start)
            j = drive(M.time_job(target[0], target[1], p.h), p, tick_at=tick_at)
            if not j.status.startswith("done"):
                bad.append((start, target, tick_at, j.status))
check("time: a clock tick mid-edit does not break the hour key (%d failed)" % len(bad), not bad, bad[:3])

# A recorded light-and-clock walk, when present: the hour field read off the real panel,
# keyed by the minute held at the edit's opening, steps 7 .. 11, 12 (0), and 2 .. 6 at 01:27.
# The capture is not shipped with the repository, so these checks skip without it.
capture = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "captures",
                       "lights-clock-2026-10-26.txt")
CAPTURE_CHECKS = (
    "capture: hour field at 18:25 reads 7, 8, 9, 10, 11, 12 (0)",
    "capture: hour field at 01:27 reads 2, 3, 4, 5, 6",
    "capture: the minute edit after setting 12 o'clock reads 25 ^ 0 = 25",
    "capture: the minute edit with 6 set reads 29 = 27 ^ 6 and 26 = 28 ^ 6",
)
if os.path.exists(capture):
    held, seen_hours, seen_minutes = None, [[]], []
    with open(capture) as f:
        for line in f:
            if "TIME (unchanged) edit:" in line:
                held = int(line.split("INFO:sundanceRS485:")[1].split()[1].split(":")[1])   # the clock's minute
                seen_hours.append([])
            elif "TIME hour edit:" in line and held is not None:
                seen_hours[-1].append(M.hour12_value(int(line.split("edit:")[1].split()[0]), {"minute": held}))
            elif "TIME minute edit:" in line:
                seen_minutes.append(int(line.split("edit:")[1].split()[0]))
    check(CAPTURE_CHECKS[0], [7, 8, 9, 10, 11, 0] in seen_hours, seen_hours)
    check(CAPTURE_CHECKS[1], [2, 3, 4, 5, 6] in seen_hours, seen_hours)
    check(CAPTURE_CHECKS[2], M.minute_value_for(12)(25, {}) == 25 and 25 in seen_minutes, seen_minutes)
    check(CAPTURE_CHECKS[3], [M.minute_value_for(6)(r, {}) for r in (29, 26)] == [27, 28], seen_minutes)
else:
    for name in CAPTURE_CHECKS:
        print("SKIP  %s (capture not present)" % name)
check("hour decode: 12 o'clock is 0, and 12 is not read as 12", M.hour12_value(25, {"minute": 25}) == 0
      and M.hour12_value(12 ^ 25, {"minute": 25}) is None)

p = Panel(hm=(18, 37))
j = drive(M.time_job(18, 37, p.h), p)
check("time: already right still walks in and out cleanly", j.status.startswith("done") and (p.h, p.mi) == (18, 37),
      j.status)

# A person presses MENU at the panel while the job is navigating: the job must stop, not edit.
p = Panel()
j = M.date_job(2026, 10, 26)
pending, t, interfered = None, 0.0, False
for i in range(200):
    t += 0.5
    if pending is not None:
        p.press(pending)
        pending = None
    if i == 4 and not interfered:
        p.press(M.BTN_MENU)
        interfered = True
    pending = j.on_frame(p.screen, p.edit, now=t, ctx={"minute": p.mi, "raw_screen": p.raw})
    if j.finished:
        break
check("an unexpected screen aborts the job before any edit is opened",
      j.status.startswith("failed") and p.fields is None and M.BTN_UP not in p.presses, (j.status, p.presses))

# The panel never acts on a press: the job gives up instead of pressing forever.
p = Panel(drop=1.0)
j = drive(M.date_job(2026, 10, 26), p)
check("a dead bus fails the job after a bounded number of resends",
      j.status.startswith("failed") and len(p.presses) == 1 + M.MAX_RESENDS, (j.status, len(p.presses)))

# The readings off the real panel at 7 PM (DECODING.md 3.11): hour field = hour ^ minute.
for raw, minute, hour in ((7, 0, 7), (4, 3, 7), (1, 6, 7), (14, 6, 8), (10, 14, 4), (8, 14, 6)):
    check("hour decode: f15 %d at minute %d is %d o'clock" % (raw, minute, hour),
          M.hour12_value(raw, {"minute": minute}) == hour, M.hour12_value(raw, {"minute": minute}))
check("minute decode: a TIME edit opened at 7:03 shows 4", M.minute_value_for(7)(4, {}) == 3)
check("minute decode: the walk's 9 -> 35 run at 6 PM is 15 -> 37",
      [M.minute_value_for(6)(r, {}) for r in (9, 22, 23, 35)] == [15, 16, 17, 37])

# Service timers (as edited on the panel, DECODING.md 3.12): open, step on the grid, save.
for name, start, target, want in (("FC", 40, 90, 90), ("FC", 40, 10, 10), ("H2O", 60, 120, 120),
                                  ("UV", 60, 100, 100), ("UV", 60, 30, 30), ("FC", 40, 45, None)):
    p = Panel(service=(start if name == "FC" else 40, start if name == "UV" else 60,
                       start if name == "H2O" else 60))
    j = drive(M.service_job(name, target, p.uv), p)
    got = {"FC": p.fc, "UV": p.uv, "H2O": p.h2o}[name]
    if want is None:   # off the 10-day grid: lands on a neighbour and says so
        check("service: %s %d -> %d lands on the grid next to it" % (name, start, target),
              j.status.startswith("done") and got in (40, 50) and j.landed == got, (j.status, got, j.landed))
    else:
        check("service: %s %d -> %d" % (name, start, target),
              j.status.startswith("done") and got == want and p.screen == M.SCREEN[name], (j.status, got))
p = Panel(service=(40, 60, 60))
j = drive(M.service_job("UV", 100, p.uv), p)
check("service: after a UV save the job reads the screen under the NEW key", j.status.startswith("done"), j.status)
for bad in (("FC", 200), ("UV", 300), ("PF", 10)):
    try:
        M.service_job(bad[0], bad[1], 60)
        check("rejects service %r" % (bad,), False, "accepted")
    except ValueError:
        check("rejects service %r" % (bad,), True)

for bad in ((2026, 2, 30), (2026, 13, 1), (1999, 1, 1)):
    try:
        M.date_job(*bad)
        check("rejects %r" % (bad,), False, "accepted")
    except ValueError:
        check("rejects %r" % (bad,), True)


# --- the same job through SundanceRS485, fed real-shaped C4 frames -----------------------------
def frame(fields):
    payload = bytearray()
    for v in fields:
        payload += bytes([0, v ^ 1])
    return bytes(5) + payload + bytes(2)


BASE = [21, 0, 0, 250, 5, 99, 32, 194, 96, 26, 0, 46, 0, 56, 96, 0]


def c4(p, settemp=96):
    f = list(BASE)
    f[0], f[11] = p.h ^ 6, p.mi
    f[7], f[9] = (p.d << 3) ^ p.mo, p.y - 2000
    f[13], f[15], f[8] = p.raw, p.edit, settemp
    return frame(f)


spa = S.SundanceRS485("127.0.0.1")
spa.log.setLevel(50)
spa.connected, spa.channel = True, 17
spa.today = lambda: date(2026, 10, 1)
p = Panel(ymd=(2026, 10, 25), hm=(18, 40))
asyncio.run(spa.parse_C4status_update(c4(p)))
asyncio.run(spa.set_time(18, 45))
asyncio.run(spa.send_temp_change(100))      # queued behind? no - it is pending, so it goes first
seen = []
for i in range(400):
    asyncio.run(spa.parse_C4status_update(c4(p, settemp=96 if not seen.count(S.BTN_TEMP_UP) else 100)))
    while not spa.queue.empty():
        b = spa.queue.get()[5]
        seen.append(b)
        p.press(b)
    if spa.menuStatus.startswith(("done", "failed")) and p.screen != M.HOME:
        p.timeout()        # the real panel goes home ~5 s after the last press
    if spa.menuStatus.startswith(("done", "failed")):
        asyncio.run(spa.parse_C4status_update(c4(p, 100)))
        break
check("spa: a pending set-point change finishes before the menu job starts",
      seen[:2] == [S.BTN_TEMP_UP, M.BTN_MENU] and spa.targetTemp == S.NO_CHANGE_REQUESTED, seen)
check("spa: it learned the UV days from the idle display field", spa.uvDays == p.uv, spa.uvDays)
check("spa: the time is set and reads back", spa.menuStatus.startswith("done") and "reads back" in spa.menuStatus
      and (spa.time_hour, spa.time_minute) == (18, 45), spa.menuStatus)

# A UV change through the spa object: the display key changes at the save, mid-job.
spa = S.SundanceRS485("127.0.0.1")
spa.log.setLevel(50)
spa.connected, spa.channel = True, 17
spa.today = lambda: date(2026, 10, 1)
p = Panel(service=(40, 60, 60))
asyncio.run(spa.parse_C4status_update(c4(p)))
asyncio.run(spa.set_service_days("UV", 90))
for i in range(400):
    asyncio.run(spa.parse_C4status_update(c4(p)))
    while not spa.queue.empty():
        p.press(spa.queue.get()[5])
    if spa.menuJobs and spa.menuJobs[0].status.startswith("done") and p.screen != M.HOME:
        p.timeout()
    if spa.menuStatus.startswith(("done", "failed")):
        break
check("spa: UV 60 -> 90 is saved and reads back under the new key",
      spa.menuStatus.startswith("done") and "reads back" in spa.menuStatus and p.uv == 90 and spa.uvDays == 90
      and spa.panelState == M.HOME, (spa.menuStatus, p.uv, spa.uvDays, spa.panelState))

# A pump press must not go out while someone is in a menu - Pump 1 is the menu's accept key.
spa = S.SundanceRS485("127.0.0.1")
spa.log.setLevel(50)
spa.connected, spa.channel = True, 17
p = Panel()
asyncio.run(spa.parse_C4status_update(c4(p)))      # idle: the bridge learns the UV key
p.screen = M.SCREEN["DATE"]
spa.lastPressAt = __import__("time").time()        # someone pressed MENU at the panel
asyncio.run(spa.parse_C4status_update(c4(p)))
asyncio.run(spa.change_pump(0, 1))
for _ in range(5):
    asyncio.run(spa.parse_C4status_update(c4(p)))
check("spa: no pump press while the panel shows a menu screen", spa.queue.empty(),
      [m[5] for m in spa.queue.queue])
p.screen = M.HOME
for _ in range(3):
    asyncio.run(spa.parse_C4status_update(c4(p)))
check("spa: ... and it goes out once the panel is home", [m[5] for m in spa.queue.queue][:1] == [S.BTN_P1],
      [m[5] for m in spa.queue.queue])

# Heat mode (DECODING.md 3.11): the HEAT edit was never recorded, so no change is driven.
check("heat: the driver knows no HEAT edit state (none was recorded)",
      "HEAT" not in M.OPENED and not any(c in M.EDIT_CODES for c in (M.SCREEN["HEAT"] + 1, M.SCREEN["HEAT"] + 2)))
for mode, current in (("ECO", 32), ("DAY", 32), ("AUTO", 34)):
    try:
        M.heat_mode_job(mode, current)
        check("heat: %s from f6 %d is refused" % (mode, current), False, "accepted")
    except M.NotObserved as e:
        check("heat: %s from f6 %d is refused, naming the missing capture" % (mode, current),
              "never been recorded" in str(e) and "Needed:" in str(e), str(e))
check("heat: an already-right mode needs no job", M.heat_mode_job("auto", 32) is None)
for mode, current in (("BOOST", 32), ("AUTO", -1), ("ECO", 16)):
    try:
        M.heat_mode_job(mode, current)
        check("heat: rejects %r with f6 %r" % (mode, current), False, "accepted")
    except M.NotObserved:
        check("heat: rejects %r with f6 %r as bad input, not as unrecorded" % (mode, current), False, "NotObserved")
    except ValueError:
        check("heat: rejects %r with f6 %r" % (mode, current), True)

spa = S.SundanceRS485("127.0.0.1")
spa.log.setLevel(50)
spa.connected, spa.channel = True, 17
p = Panel()
asyncio.run(spa.parse_C4status_update(c4(p)))          # f6 = 32 AUTO in BASE
asyncio.run(spa.set_heat_mode("AUTO"))
for _ in range(5):
    asyncio.run(spa.parse_C4status_update(c4(p)))
check("spa: heat mode already AUTO - reported, no job, no press",
      spa.menuStatus.startswith("done") and "already AUTO" in spa.menuStatus and not spa.menuJobs
      and spa.queue.empty(), (spa.menuStatus, spa.menuJobs, list(spa.queue.queue)))
try:
    asyncio.run(spa.set_heat_mode("ECO"))
    check("spa: heat mode ECO is refused", False, "accepted")
except M.NotObserved:
    for _ in range(5):
        asyncio.run(spa.parse_C4status_update(c4(p)))
    check("spa: heat mode ECO is refused and nothing is queued or pressed",
          not spa.menuJobs and spa.queue.empty(), (spa.menuJobs, list(spa.queue.queue)))

if __name__ == "__main__":
    ok = all(results)
    print("\n%s" % ("all cases passed" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)

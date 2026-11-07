"""Setting the Sundance 780's clock and date by walking its panel menus.

Everything here was observed with a person working the panel while the bridge logged
every press and status frame (DECODING.md section 3.11). Nothing in it is guessed from the code
space: each button, screen code and edit state below was seen doing what it is used for.

The driver is closed-loop. Every press names the screen it must produce; a press the spa has
not acted on (state unchanged after RETRY_FRAMES status frames) is sent again, and any OTHER
state aborts the job. An abort leaves the panel to time out back to the temperature screen by
itself, which it does about 5 s after the last press - but a field already changed in an open
edit is KEPT by that timeout (a date edit that times out keeps the year already set).

No I/O here: SundanceRS485 feeds each status frame to MenuJob.on_frame() and sends the button it
returns. tools/test_menu.py drives it against a simulated panel.
"""
import calendar
import time

BTN_MENU = 254
BTN_UP = 225     # the temperature buttons: on a menu screen UP opens the edit, again enters it
BTN_DOWN = 226
BTN_SELECT = 228  # Pump 1: inside an edit it accepts the field and moves to the next

# The display field (f13) is a STATE NUMBER XOR THE UV DAYS (DECODING.md 3.12). On the home
# screen the state is 0, so f13 there IS the UV countdown; every menu screen and edit state is
# a small fixed number. Found when UV was changed 56 -> 60 and every code moved by 56 ^ 60 = 4;
# the walk's codes were these numbers XOR 56 (and in a map taken with the UV days at 36, these XOR 36).
HOME = 0
SET_TEMP = 1      # the set point flashing after a temperature press
MENU_ORDER = ["PF", "SF", "HEAT", "FC", "UV", "H2O", "TIME", "DATE", "TEMP", "LANG", "LOCK"]
SCREEN = {"PF": 7, "SF": 11, "HEAT": 14, "FC": 17, "UV": 20, "H2O": 23,
          "TIME": 26, "DATE": 31, "TEMP": 36, "LANG": 39, "LOCK": 42}
# UP on a settings screen opens its first field UNCHANGED (screen + 1); the next UP steps it
# and the state becomes the CHANGED one (screen + 2) - as the home screen goes 0 -> 1 while the
# set point moves. Pump 1 accepts a field and moves on; after the last it saves.
OPENED = {name: SCREEN[name] + 1 for name in ("FC", "UV", "H2O", "TIME", "DATE")}
SERVICE_EDIT = {name: SCREEN[name] + 2 for name in ("FC", "UV", "H2O")}   # the days, f15 plain
TIME_EDIT = (28, 29, 30)     # hour (12-hour), minute, AM/PM
DATE_EDIT = (33, 34, 35)     # year (two digits), month, day - f15 plain
SCREEN_NAME = {v: k for k, v in SCREEN.items()}
SCREEN_NAME.update({HOME: "HOME", SET_TEMP: "SET TEMP"})
for _name, _code in OPENED.items():
    SCREEN_NAME[_code] = "%s (unchanged)" % _name
for _name, _code in SERVICE_EDIT.items():
    SCREEN_NAME[_code] = "%s days" % _name
for _code, _name in zip(TIME_EDIT + DATE_EDIT, ("TIME hour", "TIME minute", "TIME AM/PM",
                                                "DATE year", "DATE month", "DATE day")):
    SCREEN_NAME[_code] = _name
EDIT_CODES = frozenset(OPENED.values()) | frozenset(SERVICE_EDIT.values()) | \
    frozenset(TIME_EDIT + DATE_EDIT)
# Where a set-point or pump press would edit a setting instead (Pump 1 accepts a field):
# every state but HOME and SET_TEMP. Unlisted states (PF, SF, HEAT... edits) count as menus too.
NOT_MENU = frozenset((HOME, SET_TEMP))
# Service-timer ranges, from the owner's manual (FC to 180, UV to 365, both in 10-day steps)
# and what fits: the UV days are also the display key, a byte, so UV stops at 250 here.
SERVICE_MAX = {"FC": 180, "UV": 250, "H2O": 360}

# A press goes out on the bridge's next clear-to-send, so its effect shows a few frames later.
# Resend only when BOTH have passed - a resend of a press that was merely slow steps twice.
RETRY_FRAMES = 3     # status frames to wait for a press to show before sending it again
RETRY_SECONDS = 1.5  # ... and seconds; the panel's own timeout is ~5 s from the last press
MAX_RESENDS = 3      # per press while navigating; past this the job stops before editing
# Inside an open edit, stopping is the worse choice: the panel's timeout KEEPS a half-set value
# (a timed-out date edit keeps its year), so the job keeps resending as long as the edit stays
# open. A press that is merely slow is safe to resend - the next frame's value says where it is.
MAX_RESENDS_IN_EDIT = 20
MAX_ADJUST = 80      # presses inside one field before giving up
OPEN_SLACK = 2       # an edit has opened showing up to this many steps past the current value
START_FRAMES = 200   # how long a queued job waits for an idle panel before giving up


# The TIME fields are each XORed with the OTHER one. The panel shows a 12-hour clock with an
# AM/PM field, and in the hour field f15 = hour XOR the spa's
# current minute: 7, 4, 1 at 7 PM at minutes 0, 3, 6; 14 = 8 ^ 6 one step up; and the walk's
# 10, 11, 8 = 4, 5, 6 PM at minute 14. In the minute field f15 = minute XOR the hour: the walk's
# 23 steps decode with the 6 PM being set, and a TIME edit opened at 7:03 shows 4 = 3 ^ 7. DECODING.md 3.11.
def hour12_value(raw, ctx):
    v = raw ^ ctx["minute"]
    return v if 1 <= v <= 12 else None


def minute_value_for(h12):
    def minute_value(raw, ctx):
        v = raw ^ h12
        return v if v <= 59 else None
    return minute_value


def plain(raw, ctx):
    return raw


class Press:
    """Press a button and expect the panel to reach state `screen`.

    `rekey`: the UV days the display key becomes on this press - the UV save. The expected state
    is then read under the NEW key (ctx["raw_screen"] ^ rekey), and the job reports the new key
    in `MenuJob.new_uv` so the spa decodes the frames after it right.
    """
    def __init__(self, button, screen, rekey=None):
        self.button, self.screen, self.rekey = button, screen, rekey


class Adjust:
    """Step the value in an open edit to its target with UP / DOWN.

    `readings` is [(decode, target), ...]: one entry per hypothesis about how f15 encodes the
    value. Every live hypothesis has to move by exactly one in the pressed direction, and all of
    them have to reach their target, before the field counts as set.

    `grid`: the field moves in steps larger than one (the service timers: 10 days). A press then
    only has to move the value the pressed way; past the target it stops on the nearer of the two
    values either side, and `MenuJob.landed` says where.

    `opening` is [expected value per reading] or None: what each hypothesis must read when the
    edit opens - the value the spa already reports, or up to OPEN_SLACK more (the press that
    opened the edit has been seen to step it too). A hypothesis that does not is dropped before
    any press, so two that would step in opposite directions never both drive.
    """
    def __init__(self, screen, readings, label, opening=None, period=None, grid=False):
        self.screen, self.readings, self.label = screen, list(readings), label
        self.opening, self.period = opening, period
        self.grid = grid
        self.opened = False
        self.settle = None      # grid: the value to step back to after passing the target


class MenuJob:
    def __init__(self, name, steps, check=None, precheck=None):
        self.name = name
        self.steps = steps
        self.check = check          # spa -> None if the result reads back right, else a reason
        self.precheck = precheck    # spa -> None if the job may start now, else a reason
        self.status = "queued"
        self.started = False
        self.idle_frames = 0
        self._i = 0
        self._sent = None           # (button, screen, edit) of the press awaiting its effect
        self._wait = 0
        self._sent_at = 0.0
        self._resends = 0
        self._adjusts = 0
        self._ctx = {}
        self.new_uv = None          # set when a Press with rekey has landed
        self._sent_dir = None       # grid: direction of the last adjust press
        self._prev_value = None
        self.landed = None          # grid: the value a field settled on, if not the target

    @property
    def finished(self):
        return self.status.startswith(("done", "failed"))

    def fail(self, why):
        self.status = "failed: %s - %s" % (self.name, why)
        return None

    def on_frame(self, screen, edit, now=None, ctx=None):
        """Feed one status frame; return the button to press now, or None.

        `ctx` carries the frame's other decoded fields a value decode needs ({"minute": f11}).
        """
        now = time.monotonic() if now is None else now
        self._ctx = ctx or {}
        if self.finished:
            return None
        if self._sent is not None:
            button, was_screen, was_edit = self._sent
            if (screen, edit) == (was_screen, was_edit):
                self._wait += 1
                if self._wait < RETRY_FRAMES or now - self._sent_at < RETRY_SECONDS:
                    return None
                self._resends += 1
                if self._resends > (MAX_RESENDS_IN_EDIT if screen in EDIT_CODES else MAX_RESENDS):
                    return self.fail("the spa did not act on button %d on %s"
                                     % (button, SCREEN_NAME.get(screen, screen)))
                self._wait = 0
                self._sent_at = now
                return button
            self._sent = None
            step = self.steps[self._i]
            if isinstance(step, Press):
                if step.rekey is not None and "raw_screen" in self._ctx:
                    screen = self._ctx["raw_screen"] ^ step.rekey
                if screen != step.screen:
                    return self.fail("expected %s, the panel shows %s" % (
                        SCREEN_NAME.get(step.screen, step.screen), SCREEN_NAME.get(screen, screen)))
                if step.rekey is not None:
                    self.new_uv = step.rekey
                self._i += 1
            else:
                if screen != step.screen:
                    return self.fail("left the %s edit unexpectedly (%s)" % (
                        step.label, SCREEN_NAME.get(screen, screen)))
                d = 1 if button == BTN_UP else -1
                c = self._ctx

                def moved(dec):
                    a, b = dec(was_edit, c), dec(edit, c)
                    if a is None or b is None:
                        return False
                    return (b - a) * d > 0 if step.grid else b == a + d
                step.readings = [(dec, t) for dec, t in step.readings if moved(dec)]
                if not step.readings:
                    return self.fail("the %s value moved in a way no decode explains (%d -> %d)"
                                     % (step.label, was_edit, edit))
        if self._i >= len(self.steps):
            self.status = "done: %s saved" % self.name
            return None
        return self._act(screen, edit, now)

    def _act(self, screen, edit, now):
        step = self.steps[self._i]
        if isinstance(step, Press):
            if step.rekey is not None and self.landed is not None:
                step.rekey = self.landed    # UV saved where the grid landed, not the target
            return self._press(step.button, screen, edit, now)
        if screen != step.screen:
            return self.fail("not in the %s edit (%s)" % (step.label, SCREEN_NAME.get(screen, screen)))
        if not step.opened:
            step.opened = True
            if step.opening is not None:
                kept = []
                for (dec, t), want, period in zip(step.readings, step.opening, step.period):
                    v = dec(edit, self._ctx)
                    if v is not None and (v - want) % period <= OPEN_SLACK:
                        kept.append((dec, t))
                step.readings = kept
                if not kept:
                    return self.fail("the %s edit opened at %d, which matches no decode of the "
                                     "current value" % (step.label, edit))
        c = self._ctx
        live = [(dec(edit, c), t) for dec, t in step.readings if dec(edit, c) is not None]
        if not live:
            return self.fail("the %s value %d decodes under no hypothesis" % (step.label, edit))
        if step.grid:
            v, t = live[0]
            if step.settle is not None:
                if v != step.settle:
                    return self.fail("the %s did not step back to %d (reads %d)" % (step.label, step.settle, v))
                self.landed = v
                live = [(v, v)]
            elif v != t and self._sent_dir is not None and (t - v) * self._sent_dir < 0:
                # Passed the target: the panel's steps do not land on it. Stop on the nearer side.
                prev = self._prev_value
                if abs(prev - t) < abs(v - t):
                    step.settle = prev
                    return self._press(BTN_DOWN if self._sent_dir > 0 else BTN_UP, screen, edit, now)
                self.landed = v
                live = [(v, v)]
        if all(v == t for v, t in live):
            self._sent_dir = None
            self._i += 1
            if self._i >= len(self.steps):
                self.status = "done: %s saved" % self.name
                return None
            return self._act(screen, edit, now)
        v, t = live[0]
        self._adjusts += 1
        if self._adjusts > MAX_ADJUST:
            return self.fail("too many presses setting the %s" % step.label)
        self._sent_dir = 1 if v < t else -1
        self._prev_value = v
        return self._press(BTN_UP if v < t else BTN_DOWN, screen, edit, now)

    def _press(self, button, screen, edit, now):
        self._sent = (button, screen, edit)
        self._sent_at = now
        self._wait = 0
        self._resends = 0
        self.status = "running: %s" % self.name
        return button


def _to(screen_name):
    """MENU presses from HOME to a screen, each checked."""
    n = MENU_ORDER.index(screen_name) + 1
    return [Press(BTN_MENU, SCREEN[name]) for name in MENU_ORDER[:n]]


def date_job(year, month, day):
    if not 2000 <= year <= 2099:
        raise ValueError("year %d is outside what the spa stores (2000-2099)" % year)
    calendar.monthrange(year, month)   # raises on a bad month
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        raise ValueError("%04d-%02d has no day %d" % (year, month, day))
    want = (year, month, day)
    steps = _to("DATE") + [
        Press(BTN_UP, OPENED["DATE"]),
        Press(BTN_UP, DATE_EDIT[0]),
        Adjust(DATE_EDIT[0], [(plain, year - 2000)], "year"),
        Press(BTN_SELECT, DATE_EDIT[1]),
        Adjust(DATE_EDIT[1], [(plain, month)], "month"),
        Press(BTN_SELECT, DATE_EDIT[2]),
        Adjust(DATE_EDIT[2], [(plain, day)], "day"),
        Press(BTN_SELECT, SCREEN["DATE"]),
    ]

    def check(spa):
        got = (spa.year, spa.month, spa.day)
        return None if got == want else "the spa now reads %s" % (got,)
    return MenuJob("date %04d-%02d-%02d" % want, steps, check=check)


def time_job(hour, minute, now_hour):
    """`now_hour` is the spa's own hour (f0, a confirmed decode) when the job is queued."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("%r:%r is not a time" % (hour, minute))
    h12 = hour % 12 or 12
    now12 = now_hour % 12 or 12
    steps = _to("TIME") + [
        Press(BTN_UP, OPENED["TIME"]),
        Press(BTN_UP, TIME_EDIT[0]),     # steps the hour once, and marks it changed
        # The hour opens one step past the spa's own (the UP that marked it changed), up to
        # OPEN_SLACK more if a press was doubled; (v - want) % 12 counts steps forward.
        Adjust(TIME_EDIT[0], [(hour12_value, h12)], "hour", opening=[now12], period=[12]),
        Press(BTN_SELECT, TIME_EDIT[1]),
        Adjust(TIME_EDIT[1], [(minute_value_for(h12), minute)], "minute"),
        Press(BTN_SELECT, TIME_EDIT[2]),
        Press(BTN_SELECT, SCREEN["TIME"]),
    ]

    def precheck(spa):
        # The AM/PM field has one reading (1 = PM) and where the hour wraps (11 -> 12 -> 1, and
        # does AM/PM flip with it?) is unseen, so stay inside 1..11 of one half of the day -
        # counting the step the opening UP makes, so 11 o'clock is out as a start too.
        cur = spa.time_hour
        if cur // 12 != hour // 12 or not 1 <= cur % 12 <= 10 or not 1 <= hour % 12 <= 11:
            return ("moving the hour from %02d to %02d would cross noon/midnight or pass "
                    "12 o'clock, and the AM/PM field is not decoded yet" % (cur, hour))
        return None

    def check(spa):
        got = (spa.time_hour, spa.time_minute)
        # Allow the minute to have ticked over while the presses went out.
        if got[0] == hour and (got[1] - minute) % 60 in (0, 1):
            return None
        return "the spa now reads %02d:%02d" % got
    return MenuJob("time %02d:%02d" % (hour, minute), steps, check=check, precheck=precheck)


def service_job(name, days, uv_now):
    """FC, UV or H2O days. Recorded by stepping each on the panel (FC 20 -> 30 -> 40, UV 56 ->
    60, H2O 30 -> 60): UP opens the field showing the current days, the next UP steps it, UP / DOWN
    step it on the panel's grid, Pump 1 saves and returns to the screen. `uv_now` is the display
    key before the job; saving UV changes it."""
    if name not in SERVICE_MAX:
        raise ValueError("no service timer %r" % name)
    if not 0 <= days <= SERVICE_MAX[name]:
        raise ValueError("%s days must be 0-%d" % (name, SERVICE_MAX[name]))
    attr = {"FC": "filterDays", "UV": "uvDays", "H2O": "waterDays"}[name]
    steps = _to(name) + [
        Press(BTN_UP, OPENED[name]),
        Press(BTN_UP, SERVICE_EDIT[name]),
        Adjust(SERVICE_EDIT[name], [(plain, days)], "%s days" % name, grid=True),
        Press(BTN_SELECT, SCREEN[name], rekey=days if name == "UV" else None),
    ]
    job = MenuJob("%s %d days" % (name, days), steps)

    def check(spa):
        want = job.landed if job.landed is not None else days
        got = getattr(spa, attr)
        return None if got == want else "the spa now reads %s %s" % (name, got)
    job.check = check
    return job

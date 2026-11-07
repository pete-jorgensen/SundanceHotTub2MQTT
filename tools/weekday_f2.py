"""Offline test: are f2 bits 0-2 the weekday (Sunday = 0)?

Reads saved bridge logs (lines carrying a decoded `C4: [..16 fields..]`), decodes the spa's
own date from f7 = (day << 3) ^ month and f9 = year - 2000, and compares f2 & 7 with the
weekday of every date candidate.
"""
import datetime
import re
import sys
from collections import Counter, defaultdict

FRAME = re.compile(r"C4: \[([0-9, ]+)\]")


def candidates(f7, f9):
    out = []
    for month in range(1, 13):
        v = f7 ^ month
        if v & 7:
            continue
        day = v >> 3
        try:
            out.append(datetime.date(2000 + f9, month, day))
        except ValueError:
            pass
    return out


def weekday_sun0(d):
    return (d.weekday() + 1) % 7  # Python Mon=0 -> Sunday=0


def main(paths):
    total = 0
    per_state = defaultdict(Counter)  # (file, f7, f9) -> Counter of f2&7
    verdict = Counter()
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = FRAME.search(line)
                if not m:
                    continue
                f = [int(x) for x in m.group(1).split(",")]
                if len(f) != 16:
                    continue
                total += 1
                per_state[(name, f[7], f[9])][f[2] & 7] += 1
    print(f"frames read: {total}")
    for (name, f7, f9), lows in sorted(per_state.items()):
        cands = candidates(f7, f9)
        desc = ", ".join(f"{d.isoformat()} {d.strftime('%a')}={weekday_sun0(d)}" for d in cands)
        n = sum(lows.values())
        for low, k in sorted(lows.items()):
            hit = [d for d in cands if weekday_sun0(d) == low]
            status = "MATCH" if hit else "NO MATCH"
            verdict[status] += k
            print(f"{name}: f7={f7} f9={f9} f2&7={low} n={k}/{n} -> {status}; candidates: {desc}")
    print(dict(verdict))


if __name__ == "__main__":
    main(sys.argv[1:])

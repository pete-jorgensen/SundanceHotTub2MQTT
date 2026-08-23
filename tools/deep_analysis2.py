#!/usr/bin/env python3
"""Follow-ups to deep_analysis.py, with sample-size gating.

A bit that is almost always 0 "agrees" ~100% with any other rarely-set bit, and
three days of capture make any daily counter correlate with any other. Both
traps are live in this data, so every association here is gated on how often the
minority state actually occurred.
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

ts = None
rows = []
for line in open(sys.argv[1]):
    m = re.search(r"Time: ([\d\-]+ [\d:]+)", line)
    if m:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        continue
    m = re.search(r"C4: \[([0-9, ]+)\]", line)
    if m and ts:
        rows.append((ts, [int(x) for x in m.group(1).split(",")]))

bit = lambda r, f, b: (r[f] >> b) & 1

print("=" * 78)
print("[A] IS AUTOMATIC CIRCULATION ON A CLOCK SCHEDULE?")
print("=" * 78)
print("f0.b3 (bit 3 of the spa hour) matched f10.b7 (auto circulation) in 99.9%")
print("of frames. Bit agreement is a weak claim; the real test is the schedule.\n")
by_hour = defaultdict(lambda: [0, 0])
for _, r in rows:
    h = r[0] ^ 6
    by_hour[h][bit(r, 10, 7)] += 1
print("    spa hour   frames   auto-circ ON   %%")
for h in sorted(by_hour):
    off, on = by_hour[h]
    n = off + on
    print("    %02d:00      %-8d %-14d %.0f%%" % (h, n, on, 100.0 * on / n))
on_hours = sorted(h for h in by_hour if by_hour[h][1] > 0)
print("\n    hours with ANY auto circulation: %s" % on_hours)
print("    hours with NO auto circulation : %s" % sorted(h for h in by_hour if by_hour[h][1] == 0))

print("\n" + "=" * 78)
print("[B] FIELD 14 BIT STRUCTURE — which bits are temperature and which are flags?")
print("=" * 78)
print("f14 spans 64..193, but a water temperature needs only ~7 bits. Testing each")
print("bit against known state, gated on the minority count.\n")
STATES = {
    "circ running (f4.b6)": lambda r: bit(r, 4, 6),
    "auto circ (f10.b7)": lambda r: bit(r, 10, 7),
    "manual circ (f1.b7)": lambda r: bit(r, 1, 7),
    "heater on (f10.b6)": lambda r: bit(r, 10, 6),
}
for b in range(8):
    col = [bit(r, 14, b) for _, r in rows]
    ones = sum(col)
    if ones == 0 or ones == len(col):
        print("    f14.b%d  CONSTANT %d" % (b, col[0]))
        continue
    best = []
    for name, fn in STATES.items():
        sv = [fn(r) for _, r in rows]
        both1 = sum(1 for i in range(len(col)) if col[i] and sv[i])
        either1 = sum(1 for i in range(len(col)) if col[i] or sv[i])
        minority = min(ones, sum(sv))
        if minority < 20:
            continue          # too rare to claim anything
        j = 100.0 * both1 / either1 if either1 else 0
        best.append((j, name, minority))
    best.sort(reverse=True)
    txt = "  ".join("%s J=%.0f%% (n=%d)" % (n, j, m) for j, n, m in best[:2]) or "(no state common enough to test)"
    print("    f14.b%d  set in %-5d frames   %s" % (b, ones, txt))

print("\n    Jaccard (J) = overlap of the two 'on' sets, which - unlike raw agreement -")
print("    cannot be inflated by both bits being mostly zero.")

print("\n" + "=" * 78)
print("[C] THE SAME TEST APPLIED TO EVERY 'MIRROR' FOUND EARLIER")
print("=" * 78)
live = []
for f in range(16):
    for b in range(8):
        col = [bit(r, f, b) for _, r in rows]
        ones = sum(col)
        if 0 < ones < len(col):
            live.append((f, b, ones, col))
print("    pair                  agree%%   Jaccard%%   minority n   verdict")
out = []
for i in range(len(live)):
    for j in range(i + 1, len(live)):
        fa, ba, na, ca = live[i]
        fb, bb, nb, cb = live[j]
        agree = 100.0 * sum(1 for k in range(len(rows)) if ca[k] == cb[k]) / len(rows)
        if agree < 99.0 and agree > 1.0:
            continue
        both = sum(1 for k in range(len(rows)) if ca[k] and cb[k])
        either = sum(1 for k in range(len(rows)) if ca[k] or cb[k])
        jac = 100.0 * both / either if either else 0.0
        minority = min(na, len(rows) - na, nb, len(rows) - nb)
        verdict = ("SUPPORTED" if (jac > 90 or agree < 1.0) and minority >= 20
                   else "artifact: minority n=%d too small" % minority)
        out.append((minority, "    f%d.b%-2d ~ f%d.b%-2d      %6.1f   %6.1f     %6d       %s"
                    % (fa, ba, fb, bb, agree, jac, minority, verdict)))
for _, line in sorted(out, reverse=True):
    print(line)

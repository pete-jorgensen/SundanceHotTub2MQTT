#!/usr/bin/env python3
"""Structural analysis of decoded C4 frames — looks for what the field map misses.

Input is the bridge log (lines with `Time:` and `Full Message: C4: [...]`).
Everything here is derived from decoded fields only; questions about the raw
wire (the discarded key bytes, the dropped trailing byte) need a raw capture.
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

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

print("=" * 78)
print("%d frames, %s .. %s" % (len(rows), rows[0][0], rows[-1][0]))
print("=" * 78)

# ---------------------------------------------------------------- 1. clock
# The container clock is UTC. Validate the decoded spa clock against it, which
# also tests whether the ^6 on the hour field is a fixed key or a set of flags.
print("\n[1] CLOCK DECODE vs REAL TIME (container clock is UTC)")
offs = Counter()
for t, r in rows:
    spa_min = (r[0] ^ 6) * 60 + r[11]
    real_min = t.hour * 60 + t.minute
    offs[(spa_min - real_min) % 1440] += 1
top = offs.most_common(4)
for off, n in top:
    print("    offset %+5d min (%+.2f h)  in %d frames (%.1f%%)"
          % (off - (1440 if off > 720 else 0), (off - (1440 if off > 720 else 0)) / 60.0,
             n, 100.0 * n / len(rows)))
print("    -> a single dominant offset means hour = f0^6 is a FIXED key, not flag bits;")
print("       a smeared distribution would mean the ^6 is hiding something.")

# does any other XOR constant also produce a stable offset?
print("\n    alternative hour keys (how many produce a single offset for >95% of frames):")
for k in range(32):
    c = Counter(((r[0] ^ k) * 60 + r[11] - (t.hour * 60 + t.minute)) % 1440 for t, r in rows)
    if c.most_common(1)[0][1] > 0.95 * len(rows):
        print("        f0 ^ %-2d  -> offset %d min in %.1f%% of frames"
              % (k, c.most_common(1)[0][0], 100.0 * c.most_common(1)[0][1] / len(rows)))

# ---------------------------------------------------------------- 2. temp2
print("\n[2] FIELD 14 ('temp2') — is it a sensor, or the same temperature re-encoded?")
print("    temp1 = f5 ^ 2.  Testing f14 against temp1 by difference and by XOR.")
for circ in (0, 1):
    sub = [(r[14], r[5] ^ 2) for _, r in rows if ((r[4] >> 6) & 1) == circ]
    if not sub:
        continue
    dif = Counter(t1 - f14 for f14, t1 in sub)
    xor = Counter(t1 ^ f14 for f14, t1 in sub)
    print("    circ=%d  n=%d" % (circ, len(sub)))
    print("        temp1 - f14 : %s" % dict(dif.most_common(5)))
    print("        temp1 ^ f14 : %s" % dict(xor.most_common(5)))
    print("        distinct differences %d, distinct XORs %d  -> %s"
          % (len(dif), len(xor),
             "XOR is tighter (re-encoding)" if len(xor) < len(dif) else "difference is tighter (real sensor)"))

# ---------------------------------------------------------------- 3. bits
print("\n[3] PER-BIT CONSTANCY ACROSS EVERY FIELD (which bits are alive at all)")
print("    field  bits that ever change            bits stuck 1        bits stuck 0")
live_bits = []
for i in range(16):
    col = [r[i] for _, r in rows]
    var, one, zero = [], [], []
    for b in range(8):
        s = set((v >> b) & 1 for v in col)
        (var if len(s) > 1 else (one if s == {1} else zero)).append(b)
    live_bits += [(i, b) for b in var]
    print("    f%-5d %-34s %-19s %s"
          % (i, ",".join("b%d" % b for b in var) or "(none)",
             ",".join(str(b) for b in one) or "-",
             ",".join(str(b) for b in zero) or "-"))
print("    -> %d live bits total across the whole message" % len(live_bits))

# ---------------------------------------------------------------- 4. mirrors
print("\n[4] BITS THAT MIRROR EACH OTHER (>=99%% agreement) — redundancy or shared cause")
seen = set()
for a in range(len(live_bits)):
    for b in range(a + 1, len(live_bits)):
        fa, ba = live_bits[a]
        fb, bb = live_bits[b]
        agree = sum(1 for _, r in rows if ((r[fa] >> ba) & 1) == ((r[fb] >> bb) & 1))
        pct = 100.0 * agree / len(rows)
        inv = 100.0 - pct
        if pct >= 99.0 or inv >= 99.0:
            key = (fa, ba, fb, bb)
            if key in seen:
                continue
            seen.add(key)
            print("    f%d.b%d %s f%d.b%d   %.1f%%"
                  % (fa, ba, "==" if pct >= 99 else "!=", fb, bb, max(pct, inv)))

# ---------------------------------------------------------------- 5. cadence
print("\n[5] WHICH FIELDS MOVE TOGETHER (co-change counts between consecutive frames)")
co = Counter()
alone = Counter()
for i in range(1, len(rows)):
    changed = [f for f in range(16) if rows[i][1][f] != rows[i - 1][1][f]]
    if len(changed) == 1:
        alone[changed[0]] += 1
    for a in range(len(changed)):
        for b in range(a + 1, len(changed)):
            co[(changed[a], changed[b])] += 1
print("    fields that most often change ALONE: %s"
      % ", ".join("f%d(%d)" % (f, n) for f, n in alone.most_common(6)))
print("    field pairs that most often change TOGETHER: %s"
      % ", ".join("f%d+f%d(%d)" % (a, b, n) for (a, b), n in co.most_common(6)))

# ---------------------------------------------------------------- 6. f15/f12
print("\n[6] THE PERMANENTLY-ZERO FIELDS")
for i in (12, 15):
    print("    f%-2d distinct values across all frames: %s"
          % (i, sorted(set(r[i] for _, r in rows))))
print("    In the fold c = d[2i]^d[2i+1]^1, c==0 means the byte pair differs by exactly 1")
print("    (b = a^1). A pair that is always (a, a^1) is a strong candidate for padding")
print("    or for a field this tub does not populate.")

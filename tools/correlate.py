#!/usr/bin/env python3
"""Cross-tabulate C4 flag bits to see what each actually tracks."""
import re
import sys
from collections import Counter

rows = []
for line in open(sys.argv[1]):
    m = re.search(r"C4: \[([0-9, ]+)\]", line)
    if m:
        rows.append([int(x) for x in m.group(1).split(",")])

def bit(r, f, b):
    return (r[f] >> b) & 1

print("=== candidate 'circulation' signals, cross-tabulated ===")
sigs = {
    "f1.b5": lambda r: bit(r, 1, 5),
    "f1.b6": lambda r: bit(r, 1, 6),
    "f4.b6": lambda r: bit(r, 4, 6),
    "f10.b7": lambda r: bit(r, 10, 7),
    "f10.b6(heat)": lambda r: bit(r, 10, 6),
}
names = list(sigs)
print("%-14s %s" % ("", "  ".join("%-12s" % n for n in names)))
for a in names:
    line = "%-14s" % a
    for b in names:
        agree = sum(1 for r in rows if sigs[a](r) == sigs[b](r))
        line += " %-12s" % ("%.1f%%" % (100.0 * agree / len(rows)))
    print(line)

print("\n=== field14 (temp2) vs circ (f1.b5) vs temp1 ===")
buckets = {}
for r in rows:
    c = bit(r, 1, 5)
    buckets.setdefault(c, []).append((r[14], r[5] ^ 2))
for c in sorted(buckets):
    vals = buckets[c]
    t2 = [v[0] for v in vals]
    t1 = [v[1] for v in vals]
    diffs = Counter(v[1] - v[0] for v in vals)
    print("circ=%d  n=%-5d temp2 raw %d..%d (mean %.1f)  temp1 %d..%d (mean %.1f)"
          % (c, len(vals), min(t2), max(t2), sum(t2) / len(t2),
             min(t1), max(t1), sum(t1) / len(t1)))
    print("        temp1-temp2 diff: %s" % dict(diffs.most_common(6)))

print("\n=== field2 low bits (unmodelled) vs circ and pump0 ===")
tab = Counter()
for r in rows:
    tab[(r[2], bit(r, 1, 5), bit(r, 2, 4))] += 1
print("%-8s %-6s %-7s %s" % ("f2", "circ", "pump0", "count"))
for (v, c, p), n in sorted(tab.items(), key=lambda x: -x[1]):
    print("%-8d %-6d %-7d %d" % (v, c, p, n))

print("\n=== field13 (display) vs field7 (date) ===")
tab = Counter((r[13], r[7], r[7] >> 3) for r in rows)
print("%-9s %-7s %-6s %s" % ("display", "datefld", "day", "count"))
for (d, f7, day), n in sorted(tab.items()):
    print("%-9d %-7d %-6d %d" % (d, f7, day, n))

#!/usr/bin/env python3
"""Field-variance analysis over captured C4 decoded frames."""
import re
import sys
from collections import Counter, OrderedDict

NAMES = {
    0: "hour(^6)", 1: "pumpfield1", 2: "pumpfield0", 3: "unknown3",
    4: "tbd4", 5: "temp1(^2)", 6: "heatmode", 7: "date", 8: "settemp",
    9: "unknown9", 10: "heaterfield", 11: "minute", 12: "unknown12",
    13: "display", 14: "temp2", 15: "unused15",
}

rows = []
for line in open(sys.argv[1]):
    m = re.search(r"C4: \[([0-9, ]+)\]", line)
    if m:
        rows.append([int(x) for x in m.group(1).split(",")])

if not rows:
    sys.exit("no frames parsed")

n = len(rows)
width = max(len(r) for r in rows)
print("frames: %d   width: %d\n" % (n, width))
print("%-3s %-14s %-7s %s" % ("idx", "name", "#uniq", "values (count)"))
print("-" * 100)
for i in range(width):
    col = [r[i] for r in rows if len(r) > i]
    c = Counter(col)
    vals = ", ".join(
        "%d(%d)" % (v, k) for v, k in c.most_common(10)
    )
    if len(c) > 10:
        vals += ", ... range %d-%d" % (min(col), max(col))
    print("%-3d %-14s %-7d %s" % (i, NAMES.get(i, "?"), len(c), vals[:150]))

# bitwise view for the byte-flag candidates
print("\nbit-level view of flag-ish fields")
for i in (1, 2, 4, 10, 12, 13):
    col = [r[i] for r in rows if len(r) > i]
    bits = OrderedDict()
    for b in range(8):
        s = set((v >> b) & 1 for v in col)
        bits[b] = "const %d" % s.pop() if len(s) == 1 else "VARIES"
    print(" f%-2d %-12s %s" % (i, NAMES.get(i, "?"),
                               "  ".join("b%d=%s" % (b, v) for b, v in bits.items())))

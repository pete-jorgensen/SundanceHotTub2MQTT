#!/usr/bin/env python3
"""Timeline of a panel walk: every real panel press, interleaved with the C4 frames it caused.

Reads the bridge's own container log (the "Got Button Press" lines and the "Full Message: C4"
lines) and prints one line per press and one per C4 frame, with only the fields that moved
since the previous frame. The bridge logs a C4 frame whenever any field changes, so frame
counts can be compared across a walk.

    docker logs hottub > walk.log 2>&1
    python3 tools/panel_walk.py walk.log
"""
import re
import sys

BUTTONS = {254: "MENU", 228: "P1", 229: "P2", 225: "UP", 226: "DOWN",
           239: "CLEARRAY", 241: "LIGHT", 242: "COLOR"}

press_re = re.compile(r"Got Button Press (\d+) (\d+)")
c4_re = re.compile(r"Full Message: C4: \[([^\]]*)\]")

prev = None
for line in open(sys.argv[1], errors="replace"):
    m = press_re.search(line)
    if m:
        code = int(m.group(2))
        if code != 224:  # 224 = "nothing pressed", sent on every turn
            print("PRESS", BUTTONS.get(code, code))
        continue
    m = c4_re.search(line)
    if m:
        f = [int(x) for x in m.group(1).split(",")]
        if prev is None:
            print("C4   ", f)
        else:
            print("C4   ", " ".join("f%d=%d" % (i, v) for i, (v, p) in enumerate(zip(f, prev)) if v != p))
        prev = f

"""Offline checks behind DECODING.md section 7 (the cross-model field map), on saved captures.

Usage: python3 tools/cross_model_check.py <bridge log> [<bridge log> ...]

Reads bridge logs: `Time: ...` lines, `C4: [16 fields]` lines, and `Got Button Press A B : <hex>`.
Prints, per file:
  - every button press: fold (a^b^1), raw xor (a^b, what the bridge sends), and which
    pump / circulation bits changed in the next C4 frames within 5 s;
  - every frame where circulation (f4.b6), pump 1 (f2.b4) or pump 2 (f1.b2) changed, with
    f14 before/after and whether f14 bit 5 flipped in that frame or the next 2;
  - every f1 b5/b6 transition with its timestamp, to order b5 against b6.
"""
import datetime
import re
import sys

FRAME = re.compile(r"C4: \[([0-9, ]+)\]")
TIME = re.compile(r"Time: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
PRESS = re.compile(r"Got Button Press (\d+) (\d+) : ((?:[0-9A-F]{2} )+)")


def bit(v, b):
    return (v >> b) & 1


def load(path):
    events = []  # (kind, time, payload)
    now = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = TIME.search(line)
            if m:
                now = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                continue
            m = PRESS.search(line)
            if m:
                raw = bytes.fromhex(m.group(3))
                events.append(("press", now, (raw[5] ^ raw[6] ^ 1, raw[5] ^ raw[6])))
                continue
            m = FRAME.search(line)
            if m:
                f = [int(x) for x in m.group(1).split(",")]
                if len(f) == 16:
                    events.append(("c4", now, f))
    return events


def main(paths):
    for path in paths:
        ev = load(path)
        frames = [(t, f) for k, t, f in ev if k == "c4"]
        print(f"\n=== {path.rsplit('/', 1)[-1]}: {len(frames)} C4 frames")
        # 1. presses
        counts = {}
        for i, (k, t, p) in enumerate(ev):
            if k != "press":
                continue
            prev = next((f for kk, tt, f in reversed(ev[:i]) if kk == "c4"), None)
            after = [(tt, f) for kk, tt, f in ev[i + 1:] if kk == "c4"
                     and t and tt and (tt - t).total_seconds() <= 5]
            changes = []
            if prev:
                for tt, f in after:
                    for name, a, b in (("pump1 f2.b4", bit(prev[2], 4), bit(f[2], 4)),
                                       ("pump2 f1.b2", bit(prev[1], 2), bit(f[1], 2)),
                                       ("manual circ f1.b7", bit(prev[1], 7), bit(f[1], 7)),
                                       ("circ f4.b6", bit(prev[4], 6), bit(f[4], 6))):
                        if a != b and name not in changes:
                            changes.append(name)
            counts[p[1]] = counts.get(p[1], 0) + 1
            if changes:
                print(f"press {t}: fold={p[0]} raw={p[1]} -> {', '.join(changes)}")
        print("presses by raw code:", dict(sorted(counts.items())))
        # 2. pump / circulation edges vs f14 b5
        for i in range(1, len(frames)):
            (t0, a), (t1, b) = frames[i - 1], frames[i]
            edges = []
            if bit(a[4], 6) != bit(b[4], 6):
                edges.append(f"circ {bit(a[4], 6)}->{bit(b[4], 6)}")
            if bit(a[2], 4) != bit(b[2], 4):
                edges.append(f"pump1 {bit(a[2], 4)}->{bit(b[2], 4)}")
            if bit(a[1], 2) != bit(b[1], 2):
                edges.append(f"pump2 {bit(a[1], 2)}->{bit(b[1], 2)}")
            if not edges:
                continue
            win = [f[14] for _, f in frames[i:i + 3]]
            flip = any(bit(a[14], 5) != bit(v, 5) for v in win)
            print(f"edge {t1}: {', '.join(edges)}; f14 {a[14]} -> {win}; b5 flip within 3 frames: {flip}")
        # 3. f1 b5/b6 transitions
        for i in range(1, len(frames)):
            (t0, a), (t1, b) = frames[i - 1], frames[i]
            ch = [f"b{n} {bit(a[1], n)}->{bit(b[1], n)}" for n in (5, 6, 7) if bit(a[1], n) != bit(b[1], n)]
            if ch:
                print(f"f1 {t1}: {a[1]} -> {b[1]} ({', '.join(ch)})")
        # f14 b5 against circulation, frame-weighted, with f5 temp for V
        both = {}
        for _, f in frames:
            key = (bit(f[4], 6), bit(f[14], 5))
            both[key] = both.get(key, 0) + 1
        print("frames by (circ, f14.b5):", dict(sorted(both.items())))
        # Gal's reading: f14 = V XOR (32 while the pump runs). Undo it and compare with f5.
        plain, undone = {}, {}
        for _, f in frames:
            t1 = f[5] ^ 2
            v = f[14] & 0x7F
            c = bit(f[4], 6)
            plain[(c, t1 - v)] = plain.get((c, t1 - v), 0) + 1
            u = v ^ (32 if c else 0)
            undone[(c, t1 - u)] = undone.get((c, t1 - u), 0) + 1
        print("(circ, f5-f14) as read:", dict(sorted(plain.items())))
        print("(circ, f5-(f14 ^ 32*circ)):", dict(sorted(undone.items())))
        on = [f for _, f in frames if bit(f[2], 5)]
        print(f"f2.b5 set in {len(on)} frames; of those f15 != 0 in {sum(1 for f in on if f[15])}")
        for i in range(1, len(frames)):
            (t0, a), (t1, b) = frames[i - 1], frames[i]
            if bit(a[2], 5) != bit(b[2], 5):
                print(f"  f2.b5 {bit(a[2], 5)}->{bit(b[2], 5)} {t1}: f13 {a[13]}->{b[13]} f15 {a[15]}->{b[15]} f8 {a[8]}->{b[8]}")
        for i in range(1, len(frames)):
            (t0, a), (t1, b) = frames[i - 1], frames[i]
            if bit(a[1], 5) != bit(b[1], 5):
                print(f"  b5 edge {t1}: f1 {a[1]}->{b[1]} f2 {a[2]}->{b[2]} f4 {a[4]}->{b[4]} f10 {a[10]}->{b[10]} f13 {a[13]}->{b[13]}")


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
"""Check the per-frame mask in the Sundance C4 and CA payloads.

Reading under test: the last payload byte is a per-frame mask. XOR every payload byte with it
and most positions become a +1 sequence, s[j] = base + j (mod 256).

The capture logs hold enough to rebuild each payload up to one unknown byte k = d[0]:
  fold      f[i] = d[2i] ^ d[2i+1] ^ 1
  pattern   P[i] = d[2i] ^ d[0]
  tail      T    = d[last] ^ d[0]
so d[2i] = k ^ P[i], d[2i+1] = k ^ P[i] ^ f[i] ^ 1 and d[last] = k ^ T. The XOR with d[last]
removes k, so s[j] = d[j] ^ d[last] is exact for every logged frame.

C4 lines log only changes, so the fold and the pattern carry forward. A C4 frame is one
"Full Message" line plus the pattern line after it, or a lone pattern line. A CA line holds
all three values.

Usage: python3 tools/nonce_check.py CAPTURE [CAPTURE ...]
"""
import ast
import re
import sys
from collections import Counter

FULL = re.compile(r"Full Message: C4: (\[[^\]]*\])")
C4PAT = re.compile(r"C4 pair pattern: (\[[^\]]*\]) tail: (\[[^\]]*\])")
CA = re.compile(r":CA(\[[^\]]*\])\s+pair pattern: (\[[^\]]*\]) tail: (\[[^\]]*\])")

# One complete raw frame of each kind, as quoted in the code's docstrings.
RAW_SAMPLES = {
    "C4 sample": "AE A7 AA AB A4 A1 C9 5D A5 A1 C2 A1 9C BD CE BB E2 B9 BB AD B4 B5 A7 B7 "
                 "DF B1 B2 9B D3 8D 8E 8F 88",
    "CA sample": "8A 36 CA CB C4 C5 C6 FB C0 C1 C2 3C DC DD DE DF D8 D9 DA DB D4 D5 D6 D7 "
                 "D0 D1 D2 D3 EC",
}


def rebuild(fold, pat, tail, k=0):
    """Payload bytes for the assumed first byte k (the mask removes k later)."""
    d = []
    for f, p in zip(fold, pat):
        a = k ^ p
        d += [a, a ^ f ^ 1]
    d += [k ^ t for t in tail]
    return d


def unmask(d):
    """Every payload byte except the last, XOR the last byte."""
    n = d[-1]
    return [b ^ n for b in d[:-1]]


def best_base(s):
    """Return (base, hits) for the +1 run s[j] = base + j that fits the most positions."""
    base, hits = Counter((b - j) & 0xFF for j, b in enumerate(s)).most_common(1)[0]
    return base, hits


def parse_lists(*texts):
    return [ast.literal_eval(t) for t in texts]


def frames(path):
    """Yield (kind, fold, pattern, tail) for each frame in one capture file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    fold = pat = tail = None
    i = 0
    while i < len(lines):
        m = CA.search(lines[i])
        if m:
            yield ("CA", *parse_lists(*m.groups()))
            i += 1
            continue
        m = FULL.search(lines[i])
        if m:
            fold = ast.literal_eval(m.group(1))
            # Same frame: the summary line, then possibly its pattern line.
            j = i + 1
            if j < len(lines) and not C4PAT.search(lines[j]) and not FULL.search(lines[j]):
                j += 1
            pm = C4PAT.search(lines[j]) if j < len(lines) else None
            if pm:
                pat, tail = parse_lists(*pm.groups())
                i = j
            if pat is not None and tail is not None:
                yield ("C4", fold, pat, tail)
            i += 1
            continue
        m = C4PAT.search(lines[i])
        if m:
            pat, tail = parse_lists(*m.groups())
            if fold is not None:
                yield ("C4", fold, pat, tail)
        i += 1


def check_samples():
    print("== Raw samples ==")
    for name, text in RAW_SAMPLES.items():
        d = [int(x, 16) for x in text.split()]
        base, hits = best_base(unmask(d))
        print(f"{name}: {len(d)} bytes, last 0x{d[-1]:02X}, first 0x{d[0]:02X}, "
              f"first^last = {d[0] ^ d[-1]}; best +1 run base 0x{base:02X}, "
              f"{hits} of {len(d) - 1} positions")


def check_captures(paths):
    found = {"C4": [], "CA": []}
    for path in paths:
        for kind, fold, pat, tail in frames(path):
            found[kind].append((fold, pat, tail))
    print("\n== Logged frames ==")
    for kind, sel in found.items():
        if not sel:
            print(f"{kind}: no frames")
            continue
        tails = Counter()
        bases = Counter()
        run_hits = Counter()
        for fold, pat, tail in sel:
            tails[tuple(tail)] += 1
            base, hits = best_base(unmask(rebuild(fold, pat, tail)))
            bases[base] += 1
            run_hits[hits] += 1
        n = len(sel)
        top_tail, top_n = tails.most_common(1)[0]
        top_base, base_n = bases.most_common(1)[0]
        print(f"{kind}: {n} frames")
        print(f"  tail (last ^ first) = {top_tail[0] if len(top_tail) == 1 else list(top_tail)}"
              f" in {top_n} of {n} frames")
        print(f"  best +1 base 0x{top_base:02X} in {base_n} of {n} frames")
        print(f"  positions on the +1 run per frame: {dict(sorted(run_hits.items()))}")


def main(paths):
    check_samples()
    check_captures(paths)


if __name__ == "__main__":
    main(sys.argv[1:])

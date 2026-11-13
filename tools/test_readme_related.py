#!/usr/bin/env python3
"""Check that README.md has a Related projects section.

Each related repo needs a link and a description of at least one sentence.
Run: python3 tools/test_readme_related.py
"""
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"
REPOS = [
    "garbled1/pybalboa",
    "Iraithium/sundance-rs485-controller",
    "dhmsjs/pyjacuzzi",
    "jackbrown1993/Jacuzzi-RS485",
    "ccutrer/balboa_worldwide_app",
    "brianfeucht/esphome-balboa-spa",
]


def related_section(text):
    m = re.search(r"^## Related projects\n(.*?)(?=^## )", text, re.S | re.M)
    return m.group(1) if m else None


def main():
    section = related_section(README.read_text(encoding="utf-8"))
    failures = []
    if section is None:
        failures.append("no '## Related projects' section")
        section = ""
    bullets = [b for b in section.split("\n- ")[1:]]
    for repo in REPOS:
        url = "https://github.com/" + repo
        bullet = next((b for b in bullets if url + ")" in b), None)
        if bullet is None:
            failures.append("missing link: " + url)
            continue
        desc = re.sub(r"\[([^\]]*)\]\([^)]*\)", "", bullet.split("\n\n")[0])
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", desc) if len(s.split()) >= 4]
        if not sentences:
            failures.append("no description sentence: " + repo)
    for f in failures:
        print("FAIL", f)
    print("%d of %d related repos pass" % (len(REPOS) - len(failures), len(REPOS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

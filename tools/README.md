# Decode analysis tools

Offline tools for working on the protocol decode. None of them need the spa, a socket or
MQTT.

## Log analysis

`analyse_c4.py`, `correlate.py`, `deep_analysis.py` and `deep_analysis2.py` read the
bridge's own log format — lines containing `Full Message: C4: [...]` — so they work
directly against the container log. Feed them a captured log:

```sh
docker logs --since 48h hottub 2>&1 | grep "Full Message: C4" > frames.txt
python3 tools/analyse_c4.py frames.txt    # per-field value distribution + per-bit constancy
python3 tools/correlate.py  frames.txt    # cross-tabulate the flag bits against each other
```

**Read `DECODING.md` §0 before drawing conclusions.** The bridge only logs a frame when an
*unknown* field changed, so frames where only a known field moved are missing. "This field is
constant" and "bit A tracks bit B" survive that bias; "this pump only ran twice" does not.

`analyse_c4.py` produced the field table in `DECODING.md` §2; `correlate.py` produced the
circulation-signal matrix in §3.1 and the display-vs-date table in §3.2. The two
`deep_analysis*.py` scripts look for structure the field map misses, with sample-size gating
(§3.10).

## Parser regression check

`test_decode.py` builds synthetic raw frames that XOR-fold to known decoded arrays and runs
`parse_C4status_update` over them, asserting the decoded attributes:

```sh
python3 tools/test_decode.py
```

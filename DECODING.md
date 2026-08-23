# Protocol decode — as-built state, evidence, and open questions

The record of **what this project has decoded of the Sundance RS485 protocol**, how confident
each field is, and what is still unknown.

*Plan vs as-built:* §1–§5 are **as-built** (what the code does and what the wire shows).
§6 is **open questions** — hypotheses, not facts. Anything moved from §6 to §1–§5 must arrive
with the evidence that moved it.

---

## 0. How the evidence here was gathered

Two eras of correlation data exist:

| Source | What it is |
| --- | --- |
| original correlation sessions (January 2023) | The panel was operated by hand while the decoder logged. This is where `DISPLAY_MAP`, `HEAT_MODE_MAP` and the field map came from. The raw scrollback is not in the repository; this document is the extract of its substance. |
| direct observation of the tub | Two facts that no amount of frame analysis would have produced: the panel was **showing a filter-change alert** throughout the live capture, and the **manual circulation / ClearRay had not been run in a long time**. Both are load-bearing below — the second one invalidated a conclusion an earlier version of this document drew. |
| live container log (three days) | 1399 decoded `C4` frames pulled from the running bridge container (the full retained log), analysed for per-field variance and bit cross-correlation. The bit-correlation figures below quote the 1118-frame 48 h window they were computed on. |

⚠️ **The live sample is biased, and the bias matters.** `parse_C4status_update` only emits a log
line when `unknownChange` (no *known* field changed) **or** `displayNewData` (one of `temp2`,
`UnknownField3/9/12`, `unknownCirc` changed) — the `displayNewData = True` lines for every known
field are commented out (`sundanceRS485.py` ~L422–467). So **a frame in which only a known field
changed — a pump toggling, the set point moving — is never logged.** Conclusions of the form
"X is constant" and "bit A always equals bit B" survive this bias; conclusions of the form
"the pump only ran twice" do **not**, and are not drawn here.

---

## 1. As-built — message families

Frames are `0x7E`-delimited with a Balboa checksum. Status payloads are XOR-folded by `xormsg`
(`c[i] = data[2i] ^ data[2i+1] ^ 1`), halving 32 payload bytes into 16 decoded fields.

| Type | Name in code | Handled | Notes |
| --- | --- | --- | --- |
| `0xC4` | `STATUS_UPDATE` | yes | Main status. |
| `0xCA` | `LIGHTS_UPDATE` | partial | Light state. Field layout is largely guessed — see §3.5. |
| `0xCC` | `CC_REQ` | yes (send + observe) | Button press. How commands are issued. |
| `0x16` / `0x17` / `0x23` | — | **not handled** | Jacuzzi variants. Support was withdrawn — see §3.9. They raise the "Unknown Message" warning. |
| `0x00`–`0x07` | channel handshake | yes | Clear-to-send, channel assignment, existing-client probe. |

✅ **The message-type space is closed for this tub.** `listen()` warns on any type `> 0x07` it
does not recognise; **zero** such warnings in 48 h. Every frame this spa emits is one of the above.

---

## 2. As-built — the `C4` status frame, field by field

16 fields after `xormsg`. "Live" = observed values across the 1118-frame sample.

| # | Code name | Meaning | Confidence | Live (48 h) | On MQTT? |
| --- | --- | --- | --- | --- | --- |
| 0 | `HOUR_FIELD` | Hour, **XOR 6** | **high** | 1–23, tracks wall clock | ✅ |
| 1 | `PUMP_FIELD_1` | b2 = pump 2; **b7 = manual circulation**; b5/b6 lag circulation, unnamed | **high** for b7 — §3.1 | `0, 32, 64, 96, 160, 224` | ✅ |
| 2 | `PUMP_FIELD_0` | b4 = pump 1; b0–b2 unmodelled | medium | `0, 5, 6, 16, 22` | partly |
| 3 | `UNKOWN_FIELD_3` | unknown | none | **250**, constant | ✅ diag |
| 4 | `TBD_FIELD_4` | **b6 = circulation running** (either mode); rest constant `0b101` | **high** — §3.1 | `5, 69` | ✅ |
| 5 | `TEMP_FIELD_1` | Current temp, **XOR 2**, °F | **high** | 96–99 °F | ✅ |
| 6 | `HEAT_MODE_FIELD` | 32 AUTO / 34 ECO / 36 DAY | **high** (map from 2023) | **32**, constant | ✅ |
| 7 | `DATE_FIELD_2` | `>>3` = day of month; `&7` = month | day **high**, month **low** — §3.3 | `137, 145, 153` (days 17→19) | ✅ diag |
| 8 | `SET_TEMP_FIELD` | Set point, °F | **high** | 96, 98 | ✅ |
| 9 | `UNKOWN_FIELD_9` | unknown | none | **14**, constant | ✅ diag |
| 10 | `HEATER_FIELD_1` | b6 = heater on; **b7 = AUTOMATIC circulation** | **high** — §3.1 | `0, 128, 192` | ✅ |
| 11 | `MINUTE_FIELD` | Minute, plain | **high** | 0–59 | ✅ |
| 12 | `UNKOWN_FIELD_12` | unknown | none | **0**, constant | ✅ diag |
| 13 | `DISPLAY_FIELD` | **multiplexed** — b7 = alert showing, low 3 = alert day count; `DISPLAY_MAP` describes the *non-alert* mode | **medium**, §3.2 | `130, 131, 132` — alert mode throughout | ✅ |
| 14 | `TEMP_FEILD_2` | Second temperature sensor | medium — §3.4 | raw 64–193 | ✅ (range-validated) |
| 15 | — | never referenced | — | **0**, constant, both eras | ❌ |

---

## 3. Findings — where the current decode is wrong, thin, or redundant

### 3.1 Circulation — four distinct bits, resolved ✅

Running the ClearRay / manual circulation from Home Assistant produced a state the tub had
never been observed in. Across **all 420 circulating frames** in the retained log, manual and
auto are **perfectly complementary**:

| `f1.b7` (manual) | `f10.b7` (auto) | frames | |
| --- | --- | --- | --- |
| 0 | 1 | 417 | automatic circulation |
| 1 | 0 | 3 | manual circulation / ClearRay |

No exceptions. The truth table over the whole log:

| `f1` | `f4` | `f10` | b5 | b6 | b7 | f4.b6 | f10.b7 | n | state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 1001 | everything off |
| 96 | 69 | 128 | 1 | 1 | 0 | 1 | 1 | 411 | **auto** circulation |
| 224 | 69 | 0 | 1 | 1 | **1** | 1 | **0** | 2 | **manual** / ClearRay |
| 160 | 69 | 0 | 1 | 0 | **1** | 1 | **0** | 1 | manual, mid-transition |

**The model, as implemented:**

| bit | meaning | previously |
| --- | --- | --- |
| `f4.b6` | circulation **running**, either mode | called `unknownCirc` |
| `f10.b7` | circulation running **automatically** | masked off as "heater unknown" |
| `f1.b7` | circulation running **manually** (ClearRay) | `manualCirc` — the name was right; the bit had simply never been seen asserted |
| `f1.b5`, `f1.b6` | move with circulation but lag it across transitions | `circ_pump_status` / `autoCirc` |

⚠️ **`autoCirc` previously read `f1.b6`, the same bit as `circ_pump_status`**, so it could never
have been the auto-circulation flag whatever the wire did.

❓ **`f1.b5` vs `f1.b6` remain unnamed.** Both are 1 in steady circulation; they separate only
across transitions (the singleton `f1` values 32, 64 and 160). Exposed as diagnostics rather
than guessed at.

**Method note worth keeping.** Before this capture, these four bits agreed 99.3–100 % over 1118
frames and looked plainly redundant — an earlier version of this document said so. The
agreement was an artifact of the tub having sat in a single circulation mode for the whole
sample. **Bits that agree while only one of their causes is ever exercised carry no information
about each other.** One five-minute activation refuted what a thousand frames had appeared to
establish.

### 3.2 The display field is multiplexed — the panel is in an alert state

`DISPLAY_MAP` has 17 entries (menu screens: `TIME`, `DATE`, `PF`, `SF`, `HEAT`, `FC`, `UV`,
`H2O`, `LOCK`, units, language) derived in January 2023, when observed values were 0–62 —
**all with bit 7 clear**.

Every live value is 130/131/132 — **bit 7 set on every frame**, none in the map — so
`displayTextS` read `unknown` on every frame. The panel was, in fact, showing a
**filter-change alert** for the whole capture. That resolves it: the field carries **two
different meanings**, and bit 7 selects which.

With bit 7 set, the low 3 bits are a **countdown that decrements once per day**, at the same
midnight the day field rolls:

| `f13` | bit 7 | low 3 | spa day | frames |
| --- | --- | --- | --- | --- |
| 132 | set | 4 | 17 | 349 |
| 131 | set | 3 | 18 | 645 |
| 130 | set | 2 | 19 | 405 |

So: **`f13` bit 7 = an alert is showing; the low bits carry the alert's day count.** The 2023 map
describes the *other* mode — which screen the panel is on when it is not alerting — and is
probably still correct for it. It has simply been unusable for as long as this alert has been up.

Two things still unknown: what the countdown terminates at (128, i.e. zero), and whether other
alert types (flow, high-limit, water change) use bit 7 with different low bits or a different
encoding entirely.

### 3.3 Date: the day is solid, the month cannot be right, the year is absent

✅ **Day (`f7 >> 3`) is confirmed** — it stepped 17 → 18 → 19 across two midnights.

⚠️ **The spa clock was an hour fast** during the capture: spa `22:01` against local `21:02`, and
spa `21:29` against local `20:28` earlier the same evening — a consistent +1 h, the signature
of a daylight-saving change that was never applied on the panel. So the tub was wrong about the
time *and* the date, in two independent ways.

⚠️ **Month is `f7 & 7` — three bits, which cannot represent months 8–12.** It read `1` on every
live frame, captured in August. It has only ever been *observed* correct in January (the 2023
session decoded `1-13`, which is why it looked right). Either the month uses bits the decode
does not have, or it is not a month at all.

⚠️ **The spa's own date was wrong**: it believed it was **Jan 19** during an August capture. Its
*time* was right to the hour offset above, so the clock had been set and the date had not. That
is a defect in the tub's settings as well as an obstacle to decoding — and correcting it on the
panel is the cheapest possible test of the month encoding.

**Year is never decoded at all.** `self.year` is initialised to `-1`, `get_year()` returns it,
and nothing ever writes it.

### 3.4 `temp2` — a 7-bit temperature plus a flag, and a suspicious offset

Split by circulation state, over 1427 frames:

| circ | n | `temp1 − f14` |
| --- | --- | --- |
| 0 | 1007 | spread 0…5 °F, 9 distinct values |
| 1 | 420 | **31 or 32** in 402 of 420 (203 / 199) |

Tested both ways: differences are tighter than XORs in both buckets, so `f14` behaves like a
**real second sensor**, not the main temperature re-encoded.

✅ **Bit 7 is a flag, not temperature.** The three "raw 193" frames that looked like outliers
are `193 & 0x7F = 65`, and `65 + 32 = 97` — the main sensor exactly, to the degree. So the field
is a **7-bit temperature in bits 0–6 with a flag in bit 7**, and there was never an out-of-range
reading to defend against. ⚠️ **What the flag means is unknown** — n = 3, all during automatic
circulation. It is *not* manual circulation: those are three different frames, and the 99.6 %
"agreement" between the two is the small-sample artifact described in §3.10.

❓ **The offset while circulating is exactly 31–32, which is the Fahrenheit/Celsius zero
offset.** For an analogue sensor that is far too tight — 402 of 420 frames land on exactly one of
two adjacent integers. That smells like a unit or offset applied inconsistently in firmware
rather than a real 32 °F temperature difference (which in a hot tub would be physically absurd:
the second sensor would be reading 65 °F while the water is 97 °F). Recorded as a hint, not a
conclusion.

⚠️ **The correction used to read the *previous* frame's circulation state**, so it lagged one
frame and put `temp2` 32 °F out across every circulation transition. Fixed; covered by
`tools/test_decode.py`.

### 3.5 The light frame is decoded by position, with no recorded correlation

`parse_CA_light_status_update` carries a bare `#TODO: The rest...`. It assigns brightness, mode,
R, G, B and cycle time by byte index with no evidence trail, and captures `lightUnknown1/3/4/7/9`
that nothing reads. `lightCycleTime` and `lightUnknown9` are **the same byte** (`data[9]`).

Note the parser logs only on change, so the absence of `CA` lines in 48 h means the light state
was static, **not** that the frames are absent.

### 3.6 Everything the base class offers for settings is dead here

`BalboaSpaWifi` implements `set_time`, `change_heatmode`, `change_temperature_unit`,
`change_blower`, `change_aux`, `change_mister` and filter-cycle parsing. **All of them are
unreachable**: `SundanceRS485.send_message()` is overridden to log "Not supported with New Format
messaging" and return.

⚠️ **Consequence for every future setting:** the only way to change anything on this tub is to
emulate panel button presses (`send_CCmessage`), which for anything beyond temperature and the
pumps means **navigating the panel menus**. `BTN_MENU = 254` is defined in the code and **never
sent**. It is the single gate in front of: clock, date, both filtration cycles, heat mode, filter
/ UV / water-change intervals, temperature units, language, and panel lock.

⚠️ **Do not brute-force the button code space.** The observed codes cluster at 224–242 and 254;
the gaps are tempting and this is a device with a 4 kW heater, pumps and an ozone/UV system on
the other end. Enumerate codes only from an observed panel press.

### 3.7 The filtration schedule is on the wire, and the old hardcoded start hour was wrong ✅

Bit 3 of the hour field matched the automatic-circulation bit in 99.9 % of frames. Bit agreement
is a weak claim, so the real test is the schedule — automatic circulation by spa hour, over 1427
frames:

| spa hour | 00–05, 07 | **08** | **11** | **12** | **13** | **14** | **15** | 16–23 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frames | 263 | 16 | 1 | 165 | 225 | 4 | 8 | 638 |
| auto circ on | **0 %** | **100 %** | **100 %** | **100 %** | **100 %** | 50 % | **100 %** | **0 %** |

**Automatic circulation runs 08:00–15:59 spa time and is off every other hour** — an 8-hour daily
filtration window. (Hour 14 is 2 of 4 frames, i.e. the transition; hours 06, 09, 10 have no
frames.) With the tub's clock an hour ahead of local (§3.3), that is **07:00–15:00 local**.

This matters because `__init__` hardcodes the filtration cycle as a placeholder:
`filter1_duration_hours = 8` — **right**, by luck — and `filter1_hour`, which used to be `0` and
is now `8`. It also means the primary filtration schedule can be **read passively**, without the
panel walk that `PF` was assumed to require.

⚠️ 2.4 days of capture. Strong, not proven — the `PF` screen shows the setting directly and will
confirm it in seconds during a menu walk.

### 3.8 Wire structure — what the fold throws away

`xormsg` folds the payload in pairs: `c[i] = d[2i] ^ d[2i+1] ^ 1`. Three consequences that the
code does not acknowledge:

**(a) Half the message is discarded by construction.** 32 payload bytes become 16 values. If the
first byte of each pair is a fixed key the loss is nil; if it is a nonce, a sequence number or a
second data channel, **half of every status frame has never been looked at**. One frame cannot
separate key from data — the decisive test is whether the first-byte sequence is constant across
frames, and it needs a **raw capture** (the bridge logs decoded fields only).

**(b) One byte is silently dropped, every frame.** `read_one_message` builds `0x7E` + length +
`length` bytes, so a `C4` frame with length `0x26` is 40 bytes and the payload slice
`data[5:len-2]` is **33 bytes — odd**. `xormsg` iterates `range(0, len-1, 2)`, consuming indices
0–31 and never reading index 32. By contrast the Jacuzzi `0x16` frame has length `0x25`, giving a
**32-byte** payload with nothing left over. So the `C4` frame carries exactly one payload byte
more than the fold consumes, and nobody has ever seen it.

**(c) The per-field XOR constants say the `^1` is a simplification.** The hour needs `^6` on top
of the fold and the main temperature needs `^2`, while everything else needs nothing. That is the
signature of a per-position constant: `c[i] = d[2i] ^ d[2i+1] ^ K[i]` with `K[0] = 7`,
`K[5] = 3`, and `K[i] = 1` elsewhere. Validated for the clock: decoding `f0 ^ 6` and `f11`
against the container's own UTC clock over 1427 frames gives a **single fixed whole-hour offset (±1 min)**
(74 % / 22 % / 4 % across three adjacent minute values, which is just the two clocks ticking
independently). So the hour key is a fixed constant, not flag bits.

### 3.9 Jacuzzi support was removed — it was silently mis-decoding ✅

`listen()` used to route `0x16` to `parse_C4status_update` and `0x23` to the light parser, both of
which apply the `xormsg` fold. Comparing the payloads, using the `0x16` test vector that was
commented out in `listen()`:

| | payload | zero bytes | byte range |
| --- | --- | --- | --- |
| Sundance `0xC4` | 33 bytes | **0 of 33** | `0x5D`–`0xE2` |
| Jacuzzi `0x16` | 32 bytes | **12 of 32** | `0x00`–`0xFA` |

The `0x16` payload:

```
7E 25 FF AF 16 | 10 12 27 0B 16 42 00 26 FA 26 0A 14 01 81 00 00 42
                 01 1C 00 09 80 00 00 0A 00 00 00 FF 00 00 00 | 2F 7E
```

Runs of `00`, an `FF`, an `01`. **That is plaintext.** An XOR-obfuscated payload does not contain
twelve zero bytes, and the Sundance one contains none and spans a narrow high range. Folding a
plaintext frame produces noise — so every Jacuzzi tub running this bridge was getting confident,
wrong readings.

✅ **Removed rather than fixed**: there is no Jacuzzi here to test against, and an untestable
parser for a protocol with one sample frame would be the same mistake again. `0x16` / `0x17` /
`0x23` fall through to the "Unknown Message" warning, which is the honest outcome — visible,
rather than plausible and wrong. The frame above is kept here so anyone who wants to write a
real Jacuzzi parser has the evidence. The repository name still carries "jacuzzi" from the era
when support was claimed.

### 3.10 Everything rare in this dataset is n≈3, and any two rare things look 99.6 % alike

Three separate "findings" in this analysis were the same artifact, and it is worth naming because
it will recur:

- the four circulation bits agreeing 99.3–100 % (§3.1) — one circulation mode was ever exercised;
- `f14.b7` "==" `manualCirc` at 99.6 % — **three frames each, and not the same three**;
- `f2.b0` / `f7.b4` / `f13.b*` correlating at exactly 100 % — those fields change *once per day*,
  and the capture spans three days, so there are three independent samples, not 1427.

Raw agreement between two mostly-zero bits is inflated by the zeros. Re-running every candidate
pair with a Jaccard measure (overlap of the two "on" sets) and a minimum-minority gate leaves
**nothing unexplained** — the one survivor is the day counter versus the alert counter, which is
the expected relationship from §3.2.

**Rule for this project: gate on the number of independent state changes, not the frame count.**

---

## 4. As-built — what reaches MQTT

Published by `read_spa_data()` in `app.py`:

| Exposed | Entity |
| --- | --- |
| current temp, set point, heat action | `climate` |
| pump 1, pump 2 | 2 × `switch` |
| manual circ (the ClearRay / circ button) | `switch` |
| circ pump | `binary_sensor` |
| light on/off, brightness, effect | `light` |
| spa clock, spa date, heat mode, `temp2`, circ mode, auto / manual circ, alert + day count, light cycle time, raw `f3` / `f9` / `f12` | `sensor` / `binary_sensor`, the raw fields as disabled-by-default diagnostics |

**Published but invisible to HA:** `rgb_state_topic` is published every cycle, but
`payload['rgb_state_topic']` is commented out in the discovery config, so HA never binds it.

**Settable:** set point, pump 1, pump 2, "manual circ" (really the ClearRay/circ button),
light on/off, brightness, colour. **Nothing else** — see §3.6.

---

## 5. Known defects in the code (independent of decoding)

1. **Stale retained topics from earlier naming schemes can contradict the live ones.** In
   particular an old discovery config at `homeassistant/binary_sensor/hottub/Ciculation Pump/config`
   (note the space) shares `unique_id: hottub-CircPump` with the current `…/CiculationPump/config`;
   two configs claiming one id means Home Assistant honours one and discards the other,
   non-deterministically. If the bridge has run an older version on the same broker, clear the
   retained `hottub/#` and `homeassistant/+/hottub/#` topics once.
2. `light_state_topic` has a **trailing space inside the topic string**. It works only because
   publish and discovery share the constant.
3. `heatState2` has a getter but is **never assigned** after init.

---

## 5b. Observed button-press frames — primary data from the 2023 sessions

Four **distinct `0xCC` button-press frames captured from the panel** during the original
correlation sessions:

| direction | frame | payload byte |
| --- | --- | --- |
| 228 → 229 | `7E 07 10 BF CC 50 B5 8E` | `0x50` |
| 228 → 229 | `7E 07 10 BF CC 6D 88 2D` | `0x6D` |
| 229 → 228 | `7E 07 10 BF CC 63 87 D6` | `0x63` |
| 229 → 228 | `7E 07 10 BF CC C2 26 B5` | `0xC2` |

§1's table already documents `0xCC` as `CC_REQ` ("button press — how commands are issued");
this is the record of **which codes have been seen on this tub**. Panel-emulation work (§3.6)
needs real codes, and these are four, observed.

⚠️ **Not decoded — recorded.** Which physical button each byte corresponds to is unknown;
the transcript captured the frames without saying what was pressed. Treat the two
"directions" as the source/destination pair the bridge logged, not as a proven meaning.
⚠️ **These frames come from an old capture, so re-observe before relying on them.** The tub's
firmware and the bridge have both changed since; these are a starting hypothesis for what
to look for, not a verified map.

## 6. Open questions — hypotheses, not facts

### 6.1 What separates `f1.b5` from `f1.b6`?
The circulation model is otherwise resolved (§3.1). These two remain: both assert in steady
circulation and differ only across transitions. Likely a pump-speed or settling indicator. They
are published as diagnostics, so the HA recorder will accumulate the transitions needed.

### 6.1b Where does the alert countdown end, and do other alerts encode differently?
§3.2 settled what `f13` is. What is not settled: whether the countdown stops at 0, wraps, or
latches; and whether a flow/high-limit/water-change alert reuses bit 7 with different low bits.
The second needs a tub in a different alert state, which is not worth provoking.

### 6.2 Are `f3`, `f9`, `f12` the service counters?
The code guesses "days until water refresh / UV / filter change". They are rock-constant over
48 h, but changed across the two eras: `f3` 241 → **250**, `f9` 23 → **14**, `f12` 2 → **0**.
So they move on a scale of weeks-to-months, which fits a service counter and rules out anything
faster. Untested. Needs a **long-baseline capture** plus known service dates to correlate against,
or a panel walk through the `FC` / `UV` / `H2O` menus that shows the same numbers.

### 6.3 What are `f2` bits 0–2?
They vary (values 0, 5, 6) **independently of** circulation and both pumps. Unmodelled. Ozone,
ClearRay UV, a filtration-cycle phase and heater staging are all plausible.

### 6.4 Does `f1.b5` mean "circ mode" vs `f1.b6` "circ running"?
See §3.1. Distinguishable by watching a circulation start/stop edge closely.

### 6.5 Is there a fault/error surface at all?
Nothing in the decode covers error or diagnostic codes (flow switch, high limit, dry-fire, sensor
fault). A tub with a fault shows it on the panel, so the code exists somewhere on the wire —
most likely in `f13` or a `f2` bit. Nothing has been captured with the tub in a fault state, and
provoking one deliberately is not proposed.

# Protocol decode — as-built state, evidence, and open questions

The record of **what this project has decoded of the Sundance RS485 protocol**, how confident
each field is, and what is still unknown.

§1–§5 say what the code does and what the wire shows. §6 is **open questions**: hypotheses,
not facts.

---

## 0. How the evidence here was gathered

The evidence comes from four kinds of observation on one Sundance 780:

| Source | What it is |
| --- | --- |
| original correlation sessions | The panel was operated by hand while the decoder logged. This is where `DISPLAY_MAP`, `HEAT_MODE_MAP` and the field map came from. The raw log is not published; this document is the extract of its substance. |
| direct observation of the tub | Two facts that no amount of frame analysis would have produced: the panel was **showing a filter-change alert** throughout the long capture, and the **manual circulation / ClearRay had not been run in a long time**. Both are load-bearing below. |
| panel walks | A person at the panel naming each screen and value while the bridge logged every press and frame: every menu screen in order, PF/SF/HEAT/FC/UV/H2O values read out, the DATE and TIME set by hand, the FC/UV/H2O timers edited, and the lights stepped through their colours. `tools/panel_walk.py` turns such a log into a press/frame timeline. §3.11, §3.12. |
| a three-day container log | 1399 decoded `C4` frames from the running bridge, analysed for per-field variance and bit cross-correlation. The bit-correlation figures below quote the 1118-frame 48 h window they were computed on. |

⚠️ **The three-day log and the correlation sessions are biased, and the bias matters.** The
logging they were taken with emitted a line only when an *unknown* field (`temp2`,
`UnknownField3/9/12`, `unknownCirc`) changed. So **in those logs a frame in which only a known
field changed — a pump toggling, the set point moving — is missing.** Conclusions of the form
"X is constant" and "bit A always equals bit B" survive this bias; conclusions of the form
"the pump only ran twice" do **not**, and are not drawn here.

✅ **The bridge's own logging does not have this bias**: it logs every frame in which *any*
decoded field changed (with the list of changes), plus the folded-pair bytes whenever they
change, and `RAW_FRAME_LOG` records each frame's raw bytes.

---

## 1. As-built — message families

Frames are `0x7E`-delimited with a Balboa checksum. Status payloads are XOR-folded by `xormsg`
(`c[i] = data[2i] ^ data[2i+1] ^ 1`), halving 32 payload bytes into 16 decoded fields.

| Type | Name in code | Handled | Notes |
| --- | --- | --- | --- |
| `0xC4` | `STATUS_UPDATE` | yes | Main status. |
| `0xCA` | `LIGHTS_UPDATE` | partial | Light state. Field layout is largely guessed — see §3.5. |
| `0xCC` | `CC_REQ` | yes (send + observe) | Button press. How commands are issued. |
| `0x16` / `0x17` / `0x23` | — | **not handled** | Jacuzzi's plaintext message family. Not decoded — see §3.9. They raise the "Unknown Message" warning. |
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
| 2 | `PUMP_FIELD_0` | b4 = pump 1; **b0–b2 = day of the week, Sunday = 0** | medium; **high** for b0–b2 — §6.3 | `0, 5, 6, 16, 22` | partly |
| 3 | `UNKOWN_FIELD_3` | **FC days XOR 250** | low — two readings, §3.11 | 250 (FC 0, alert up); **238** with FC 20 | ✅ `number` |
| 4 | `TBD_FIELD_4` | **b6 = circulation running** (either mode); rest constant `0b101` | **high** — §3.1 | `5, 69` | ✅ |
| 5 | `TEMP_FIELD_1` | Current temp, **XOR 2**, °F | **high** | 96–99 °F | ✅ |
| 6 | `HEAT_MODE_FIELD` | 32 AUTO / 34 ECO / 36 DAY | **high** (map from the correlation sessions) | **32**, constant | ✅ |
| 7 | `DATE_FIELD_2` | **`(day << 3) XOR month`** | **high** — §3.3 | `137, 145, 153` (Jan 17→19); 28 (Apr 3) → **194** (Oct 25) | ✅ |
| 8 | `SET_TEMP_FIELD` | Set point, °F | **high** | 96, 98 | ✅ |
| 9 | `UNKOWN_FIELD_9` | **year − 2000** | **high** — §3.3 | 14 (2014) → 25 → **26** as the year was stepped; 23 in the earlier correlation sessions | ✅ (in the date) |
| 10 | `HEATER_FIELD_1` | b6 = heater on; **b7 = AUTOMATIC circulation** | **high** — §3.1 | `0, 128, 192` | ✅ |
| 11 | `MINUTE_FIELD` | Minute, plain | **high** | 0–59 | ✅ |
| 12 | `UNKOWN_FIELD_12` | **H2O days**, plain | low — two readings, §3.11 | 0 for 35 days → **30** after the H2O screen was visited | ✅ `number` |
| 13 | `DISPLAY_FIELD` | **panel state XOR the UV days** — on the home screen (state 0) it *is* the UV days | **high** — §3.12 | 130–132 (UV 132 counting down, three-day log); 56; 60 after UV was set to 60 | ✅ UV days, panel screen |
| 14 | `TEMP_FEILD_2` | Second temperature sensor | medium — §3.4 | raw 64–193 | ✅ (range-validated) |
| 15 | `EDIT_FIELD` | **the value on the panel while a menu field is edited**; 0 otherwise | **high** — §3.11 | 0 at rest; year/month/day plain, minute XOR 6 | ✅ diag |

---

## 3. Findings — where the current decode is wrong, thin, or redundant

### 3.1 Circulation — four distinct bits, resolved ✅

Running the ClearRay / manual circulation is what separates these bits; a tub left in automatic
circulation never shows the difference. Across **all 420 circulating frames** in the retained log, manual and
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

| bit | meaning | name in the code |
| --- | --- | --- |
| `f4.b6` | circulation **running**, either mode | `circ_pump_status` |
| `f10.b7` | circulation running **automatically** | `autoCirc` |
| `f1.b7` | circulation running **manually** (ClearRay) | `manualCirc` |
| `f1.b5`, `f1.b6` | move with circulation but lag it across transitions | `circB5`, `circB6` |

`f1.b6` is not the auto-circulation flag, though it agrees with it in steady circulation; that
is `f10.b7`.

**When the ClearRay button works** (observed at the spa's own panel):

- When the circulation pump runs because the spa is heating or the jets are on, the button
  does nothing.
- When nothing runs, a press starts the pump for a preset time. A second press stops it.
- When a new set point causes heating, the pump runs until heating completes. The user
  cannot stop it.

So circulation has three states, and the bridge applies the rule to them:

| state | bits | the manual switch |
| --- | --- | --- |
| off | `f4.b6` = 0 | ON presses ClearRay once: a manual run starts |
| manual | `f1.b7` = 1 | OFF presses ClearRay once: the run stops |
| auto (heat, filter cycle, jets) | `f4.b6` or `f10.b7` = 1, `f1.b7` = 0 | locked: the bridge presses nothing and says why in Last Command |

The lock matters because the spa ignores a ClearRay press during an automatic run. A bridge that
keeps pressing an unanswered toggle only flips it back and forth. A ClearRay press from Home
Assistant while the spa is fully idle is not yet confirmed on a live spa; that press would also
be the first confirmation that 0xEF is the ClearRay code (§6.6).

❓ **`f1.b5` vs `f1.b6` remain unnamed.** Both are 1 in steady circulation; they separate only
across transitions (the singleton `f1` values 32, 64 and 160). Exposed as diagnostics rather
than guessed at.

**Method note.** In a sample where the tub sits in one circulation mode, these four bits agree
99.3–100 % over 1118 frames and look plainly redundant. The agreement is an artifact of that
single mode. **Bits that agree while only one of their causes is ever exercised carry no
information about each other.** One five-minute manual activation separates them.

### 3.2 The display field on the home screen counts down once a day

The display field is the panel state XOR the UV days (§3.12). Bit 7 is not an alert flag: the
high values are simply a UV countdown above 127.

The correlation sessions' `DISPLAY_MAP` (17 menu screens: `TIME`, `DATE`, `PF`, `SF`, `HEAT`,
`FC`, `UV`, `H2O`, `LOCK`, units, language) holds values 0–62. In the three-day log every value
is 130/131/132, none in the map, decrementing once per day at the same midnight the day field
rolls:

| `f13` | spa day | frames |
| --- | --- | --- |
| 132 | 17 | 349 |
| 131 | 18 | 645 |
| 130 | 19 | 405 |

That is the home screen showing UV 132 → 130. The old map is right under a key: in a panel walk
every screen read at its mapped code XOR 28 (§3.11), and 28 is the UV days at the two
observations XORed together (36 ^ 56, §3.12).

**The low 3 bits of the display field over weeks.** On an idle panel they count down about one
per day, wrap from 0 to 7 on an 8-day cycle, and within one day dither between two adjacent
values. They are the low 3 bits of the UV countdown (§3.12), and a 3-bit field wraps every 8
counts by construction, so the wrap is a property of the mask, not of the spa. This reading is
not certain, because the full UV days (up to 255) were not recorded alongside. What the full
countdown does at 0 stays open (§6.1b).

### 3.3 Date: `(day << 3) XOR month`, and the year is `f9` ✅

**Setting the date on the panel decoded it** (§3.11). The tub read Apr 3 2014 (the month and
day edits opened at 4 and 3; the year field read 14) and was set to Oct 25 2026:

| date | `f7` | `(day << 3) ^ month` | `f9` |
| --- | --- | --- | --- |
| Apr 3 2014 | 28 | 24 ^ 4 = **28** | 14 |
| Oct 25 2026 | 194 | 200 ^ 10 = **194** | 26 |
| Jan 17–19 (three-day log) | 137, 145, 153 | 136^1, 144^1, 152^1 | 14 |

✅ The day steps 17 → 18 → 19 across two midnights in the three-day log. Reading `f7 >> 3` as
the day and `f7 & 7` as the month is right for months 1–7 only: a month of 8–12 sets bit 3,
which flips the day's low bit. ⚠️ **That makes one reading ambiguous** when `month & 7` is 1–4: 137 is Jan 17
*or* Sep 16. The two diverge at the next midnight (Jan 18 = 145, Sep 17 = 129), so `pick_date`
keeps every candidate and takes the one that steps by a day; with no history it takes the one
nearest today — right for a tub whose date is kept set, wrong for one stuck in 2014.

**`f9` is the year − 2000**, not a service counter: 14 for the whole three-day log (the tub's
date was unset and read 2014), 25 when a date edit timed out with the year already stepped,
26 once saved — and **23 in the earlier correlation sessions**, whose spa date read 2023.

The date fields report what the tub believes, not the real date: a tub whose date has not
been set can read a 2014 date, and its weekday field follows that date (§6.3).

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

✅ **The 31–32 offset while circulating is bit 5 of `f14`, XOR-flipped while the circulation
pump runs** (GalKar83's 680 reading, [pete-jorgensen/SundanceHotTub2MQTT#9](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/pull/9), tested on the 780). A real 32 °F
difference would be physically absurd in a hot tub, and 402 of 420 frames landing on one of two
adjacent integers is far too tight for an analogue sensor. Undoing the flip brings
`(f5 ^ 2) − f14` to −1…+3 in 424 of 424 saved frames, in both circulation states, and it flipped
at the one captured circulation start (98 → 66) but at neither pump 1 edge. The correlation
sessions read −31…−33 rather than +31…+33 because bit 5 of the underlying value V (`f14` with
the pump flip undone) was clear then. See §7.

The correction must use the *current* frame's circulation state; using the previous frame's puts
`temp2` 32 °F out across every circulation transition. Covered by `tools/test_decode.py`.

### 3.5 The light frame — the byte map, with its evidence ✅

`parse_CA_light_status_update`'s byte map was first correlated by hand. A saved capture of the
lights stepped through their colours, with each colour read out at the panel, confirms it: every
byte the map names behaves as named:

| byte | name | observed |
| --- | --- | --- |
| `data[1]` | brightness | **100, 66, 33** — three levels, in percent |
| `data[4]` | mode | **128 = colour fade**; a fixed colour has its own code: 2 blue, 7 magenta, 6 red |
| `data[2]` / `data[6]` / `data[8]` | B / G / R | fixed: blue 255/0/0, magenta 127/0/127, red 0/0/255. In fade the three sweep a colour wheel |
| `data[9]` | cycle time | 2 then 4 in fade, 0 on a fixed colour |
| `data[0]` / `data[3]` / `data[5]` | unknown | constant 189 / 60 / 255 across all 621 frames |

`data[9]` is the cycle time, held in `lightCycleTime`.

Note the parser logs only on change, so the absence of `CA` lines in 48 h means the light state
was static, **not** that the frames are absent.

### 3.6 Everything the base class offers for settings is dead here

✅ **The menus have been walked (§3.11)**, and the bridge sets the clock and the
date through them (`pybalboa/panel_menu.py`). The rule below still stands for everything not
observed: PF / SF / HEAT / TEMP / LANG / LOCK edits are **not** driven (FC / UV / H2O are,
§3.12; why HEAT is not: §3.11 *Heat mode*).

`BalboaSpaWifi` implements `set_time`, `change_heatmode`, `change_temperature_unit`,
`change_blower`, `change_aux`, `change_mister` and filter-cycle parsing. **All of them are
unreachable**: `SundanceRS485.send_message()` is overridden to log "Not supported with New Format
messaging" and return.

⚠️ **Consequence for every future setting:** the only way to change anything on this tub is to
emulate panel button presses (`send_CCmessage`), which for anything beyond temperature and the
pumps means **navigating the panel menus**. `BTN_MENU = 254` is the single gate in front of:
clock, date, both filtration cycles, heat mode, filter / UV / water-change intervals,
temperature units, language, and panel lock. The bridge sends it only for the menu walks it
drives (`pybalboa/panel_menu.py`). It has no code for a held Menu (209, §7).

⚠️ **Do not brute-force the button code space.** The observed codes cluster at 224–242 and 254;
the gaps are tempting and this is a device with a 4 kW heater, pumps and an ozone/UV system on
the other end. Enumerate codes only from an observed panel press.

### 3.7 The filtration schedule is on the wire ✅

Bit 3 of the hour field matched the automatic-circulation bit in 99.9 % of frames. Bit agreement
is a weak claim, so the real test is the schedule — automatic circulation by spa hour, over 1427
frames:

| spa hour | 00–05, 07 | **08** | **11** | **12** | **13** | **14** | **15** | 16–23 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frames | 263 | 16 | 1 | 165 | 225 | 4 | 8 | 638 |
| auto circ on | **0 %** | **100 %** | **100 %** | **100 %** | **100 %** | 50 % | **100 %** | **0 %** |

**Automatic circulation runs 08:00–15:59 spa time and is off every other hour** — an 8-hour daily
filtration window. (Hour 14 is 2 of 4 frames, i.e. the transition; hours 06, 09, 10 have no
frames.) These are hours on the spa's own clock, which need not match local time.

This matters because `__init__` hardcodes the filtration cycle as a placeholder:
`filter1_duration_hours = 8` and `filter1_hour = 8`, the values measured here. It also means the primary filtration schedule can be **read passively**, without the
panel walk that `PF` was assumed to require.

✅ **Confirmed on the panel**: the PF screen reads 8 am for 8 hours. SF reads **HOL**
(holiday), so `filter2_enabled = 0` stays until what HOL means for the second cycle is known.

### 3.8 Wire structure — what the fold throws away

✅ **What the fold discards is a per-frame key over a fixed pattern.** The first byte
of every pair, and the odd tail byte, change on **every frame** — but between any two frames
they all differ by **the same XOR** (`22^7 = 18^3 = 28^13 = … = 17`, tail `48^33 = 17`), in `C4`
and `CA` alike. So each frame is masked by a **per-frame random key byte** over a fixed pattern,
and the fold cancels the key. Relative to its own first byte the discarded half is the same
16-byte pattern frame after frame (`0, 4, 10, 28, 11, 105, 50, 216, 116, 20, 26, 33, 0, 28, 71,
32` for `C4`), and the tail byte is that key XOR a constant (38) — with one exception seen so far:
**pattern byte 11 steps with the minute** (33, 34, 35 at minutes 57, 58, 59 — the minute XOR 24),
a second copy of `f11`, which the fold already decodes. So no data the fold misses has shown up;
in particular UV is not hiding there. Which other pattern bytes copy their field is not yet
established; the bridge logs the pattern whenever it changes, and `RAW_FRAME_LOG` keeps the raw
bytes.

"Frame key" here means that per-frame byte (it changes every frame), not the daily display key
of §3.12. The tail byte is the same mask seen from the other end: `last ^ first` is 38 in 255 of
255 rebuilt `C4` frames and 102 in 622 of 622 `CA` frames (`python3 tools/nonce_check.py <logs>`).

✅ **`CA`: the last byte is a per-frame mask over a +1 position counter** (GalKar83's hypothesis,
[pete-jorgensen/SundanceHotTub2MQTT#9](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/pull/9),
confirmed on the 780's `CA` frames). XOR every payload byte `d[j]` with the last byte, then with `0x24 + j`, and
the result `x[j]` is plaintext: in 622 of 622 saved `CA` frames, 13 of 14 pairs have one byte 0
and the other byte equal to the folded light value (the mode in the first byte of its pair, the
rest in the second). Pair 0 is `0x42, 0xFF` (fold 189). So in `CA` the fold loses nothing.
❓ **`C4` does not fit that simple form.** Its bytes are stable relative to the mask, but no single
base makes a +1 run: the best (`0x28`) leaves 1266 of 8160 bytes zero over 255 frames, and no
frame has more than 8 of 32 positions on one run. What the fixed `C4` pattern is stays open.

`xormsg` folds the payload in pairs: `c[i] = d[2i] ^ d[2i+1] ^ 1`. Three consequences:

**(a) Half the message is discarded by construction.** 32 payload bytes become 16 values. As
above, the discarded half is a per-frame key over a fixed pattern, so the loss is nil apart from
the minute copy.

**(b) The `C4` payload has one byte more than the fold reads.** `read_one_message` builds
`0x7E` + length + `length` bytes, so a `C4` frame with length `0x26` is 40 bytes and the payload
slice `data[5:len-2]` is **33 bytes — odd**. `xormsg` iterates `range(0, len-1, 2)`, consuming
indices 0–31 and never reading index 32. By contrast the Jacuzzi `0x16` frame has length `0x25`,
giving a **32-byte** payload with nothing left over. The 33rd byte is the tail byte above: the
frame key XOR 38, with no data seen in it.

**(c) The per-field XOR constants say the `^1` is a simplification.** The hour needs `^6` on top
of the fold and the main temperature needs `^2`, while everything else needs nothing. That is the
signature of a per-position constant: `c[i] = d[2i] ^ d[2i+1] ^ K[i]` with `K[0] = 7`,
`K[5] = 3`, and `K[i] = 1` elsewhere. Validated for the clock: decoding `f0 ^ 6` and `f11`
against the container's own UTC clock over 1427 frames gives a **single fixed whole-hour offset (±1 min)**
(74 % / 22 % / 4 % across three adjacent minute values, which is just the two clocks ticking
independently). So the hour key is a fixed constant, not flag bits.

### 3.9 Only Sundance frames are decoded; Jacuzzi frames are plaintext ✅

Jacuzzi tubs send a different, plaintext message family (`0x16` / `0x17` / `0x23`). Routing
`0x16` to `parse_C4status_update` and `0x23` to the light parser would apply the `xormsg` fold
to them. Comparing the payloads, using a Jacuzzi `0x16` test vector:

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
plaintext frame produces noise: the XOR decoder would give a Jacuzzi tub confident, wrong
readings.

✅ **So the bridge rejects them**: there is no Jacuzzi here to test against, and an untestable
parser for a protocol with one sample frame would give the same wrong readings. `0x16` / `0x17` /
`0x23` fall through to the "Unknown Message" warning, which is the honest outcome — visible,
rather than plausible and wrong. The frame above is kept here so anyone who wants to write a
real Jacuzzi parser has the evidence.

### 3.10 Everything rare in this dataset is n≈3, and any two rare things look 99.6 % alike

Three separate "findings" in this analysis were the same artifact, and it is worth naming because
it will recur:

- the four circulation bits agreeing 99.3–100 % (§3.1) — one circulation mode was ever exercised;
- `f14.b7` "==" `manualCirc` at 99.6 % — **three frames each, and not the same three**;
- `f2.b0` / `f7.b4` / `f13.b*` correlating at exactly 100 % — those fields change *once per day*,
  and the capture spans three days, so there are three independent samples, not 1427.

Raw agreement between two mostly-zero bits is inflated by the zeros. Re-running every candidate
pair with a Jaccard measure (overlap of the two "on" sets) and a minimum-minority gate leaves
**nothing unexplained** — the one survivor is the day field versus the home-screen UV countdown,
which both step once a day (§3.2).

**Rule for this project: gate on the number of independent state changes, not the frame count.**

---

### 3.11 The panel walk — screens, buttons and the edit field ✅

A person worked the panel while the bridge logged, naming each screen and value; the log
carries each press (the bridge logs other panels' `0xCC` presses) and each frame.
`python3 tools/panel_walk.py <log>` turns such a log into a press/frame timeline.

**Buttons, all observed on this tub:** MENU = **254**; temperature UP / DOWN = 225 / 226; **Pump 1 = 228 is the menu's accept key**. The four
§5b frames are Pump 1 and Pump 2 presses (§5b says which is which).

**Screens.** MENU from the temperature screen steps through eleven screens and wraps back:

| presses | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| screen | PF | SF | HEAT | FC | UV | H2O | TIME | DATE | TEMP | LANG | LOCK | temp |
| `f13` | 63 | 51 | 54 | 41 | 44 | 47 | 34 | 39 | 28 | 31 | 18 | 56 |
| old map | 35 | 47 | 42 | 53 | 48 | 51 | 62 | 59 | 0 | 3 | 14 | 36 |

⭐ **Every code is the old map's value XOR 28**, all twelve. The old map was right; the display
field carries a key, and the key had changed between the two observations (it is the UV days, §3.12). Whether the key moves again — with firmware, with the
alert, per power cycle — is unknown, so the driver checks every screen it reaches against this
table and stops on any other code. 57 appears while the set point flashes.

**Values read off the screens:** PF 8 am / 8 h, SF HOL, HEAT AUTO, **FC 20, UV 56, H2O 30**.

**Heat mode — the screen is known, the edit is not.** Three MENU presses
reach the HEAT screen (state 14), and it showed AUTO. That is all the captures hold:

- No saved log has a HEAT edit state (15 or 16).
- f6 is **32 AUTO in every saved frame** (339 status lines across the three captures). The
  34 ECO / 36 DAY codes come from the correlation sessions, with no record of the presses.
- So it is not known which button changes the mode (UP, DOWN or Pump 1), what order the
  modes step in, whether f15 shows the mode, or how the change saves.

The driver therefore changes no heat mode. `panel_menu.heat_mode_job` and
`SundanceRS485.set_heat_mode` accept AUTO / ECO / DAY: when the spa already reads the asked mode
they say so and press nothing, and any change raises `NotObserved` with the reason. **Missing
capture:** a bridge log, with `RAW_FRAME_LOG` on, of a person changing HEAT AUTO → ECO → DAY →
AUTO at the panel, naming each press and what the screen shows. That gives the edit states, the
button, the step order and the save, and then the job can follow the FC / UV / H2O pattern.

**Editing.** On DATE or TIME, UP opens the first field *unchanged* (24 / 35); the next UP steps
it and the code turns into the *changed* one (25 / 36), as the home screen goes 56 → 57 while the
set point moves. UP / DOWN step the value one at a time; Pump 1 accepts the field and moves to
the next; the last accept saves and returns to the screen. While a field is open `f13` names it
and **`f15` carries the value**:

| `f13` | field | `f15` |
| --- | --- | --- |
| 24 | DATE year, not yet changed | not read |
| 25 | DATE year | two digits, plain (14 → 26) |
| 26 | DATE month | plain (4 → 10) |
| 27 | DATE day | plain (3 → 25) |
| 35 | TIME hour, not yet changed | as 36 |
| 36 | TIME hour | **12-hour value XOR the spa's current minute** (`f11`). At 7 PM: 7, 4, 1 at minutes 0, 3, 6 and 14 = 8 ^ 6 one step up; the walk's 10, 11, 8 at minute 14 = 4, 5, 6 PM. The panel shows "7:03" with AM/PM |
| 37 | TIME minute | **minute XOR the hour (12-hour) being set**: the walk's 23 steps decode with the 6 PM set; a TIME edit opened at 7:03 shows 4 = 3 ^ 7 |
| 38 | TIME AM/PM | **1 = PM, 0 = AM** (seen flipping PM → AM at the panel) |

**The 11 → 12 → 1 wrap and AM/PM** (a saved capture with each result read out at the panel).
**12 o'clock is 0** in the hour field: opened at
6:25 PM and stepped up, `f15` read 31, 30, 17, 16, 19, 18, 25 = 6 … 11, 0 XOR 25. The next step
reads 24 = 1 XOR 25. ⚠️ **The key is the minute held in the edit, not the live `f11`**: during
that edit `f11` ticked 25 → 26, and the 1 o'clock step still decodes with 25 (with 26 it would read 2),
and the minute saved was 25. The two only differ when the clock ticks mid-edit, which is why the
walk could not tell them apart. The saved clock hour obeys the existing `f0 = hour24 XOR 6` in
every case: 12 PM → 10, 1 PM → 11, 1 AM → 7, 6 PM → 20.

The two TIME fields are each keyed by the other — which is why the walk's minute looks like
"XOR 6" (the 6 PM being set) and its hour like "XOR 14" (minute 14). A driver that expects a
fixed key cannot read the hour field.

⚠️ **A timeout keeps a half-finished edit.** A date edit left open in the month field
keeps the year already stepped when the panel times out (`f9` 14 → 25 was seen). The driver therefore never abandons
an open edit while it can still press, and reports what the spa reads after any failure.

**Service timers, provisional (two readings each):**
- `f12` = **H2O days**, plain: 0 for a 35-day recorder window, 30 the moment the H2O
  screen was visited — a reset to the 30-day interval is the likely reading.
- `f3` = **FC days XOR 250**: 250 for 35 days while the filter-change alert was up (FC 0), 238 =
  250 ^ 20 with FC reading 20. The correlation sessions' value 241 reads as 11 days.
- **UV (56) is in no decoded field**, and not in the half the fold drops either (§3.8 — that
  half is a per-frame key). It may only be sent while the UV screen is shown, in `f15` or a
  frame type not yet seen; a `RAW_FRAME_LOG` capture across a UV screen visit will say.
- How an FC / UV / H2O *edit* behaves is in §3.12.

### 3.12 The display field is the panel state XOR the UV days ✅

Evidence: a saved bridge log of the FC / UV / H2O timers being edited at the panel.

When UV was changed from 56 to 60, **every display code after the save moved by XOR 4 — and
56 ^ 60 = 4.** The field is a **panel state number XOR the UV days**.
Confirmed under both keys: with UV 60, PF read 59 = 7 ^ 60, FC 45 = 17 ^ 60, the FC edit 46 / 47
= 18 / 19 ^ 60, and home 60 = 0 ^ 60.

So the **home screen's code is the UV countdown itself**. The correlation sessions' "Current
Temp" codes 23, 36, 32, 31 were the UV days at the time; the three-day log's 130–132 were UV
132 → 130 counting down daily (not an alert flag); the walk's "XOR 28" against the old map was
really XOR 56, the UV days during the walk.

**The states, decoded** (each is the observed code XOR the UV days at the time):

| state | screen | edit states |
| --- | --- | --- |
| 0 / 1 | home / set point flashing | |
| 7, 11, 14 | PF, SF, HEAT | (not walked; HEAT: §3.11 *Heat mode*) |
| 17 | FC | 18 opened, 19 changed |
| 20 | UV | 21, 22 |
| 23 | H2O | 24, 25 |
| 26 | TIME | 27 hour opened, 28 hour, 29 minute, 30 AM/PM |
| 31 | DATE | 32 year opened, 33 year, 34 month, 35 day |
| 36, 39, 42 | TEMP, LANG, LOCK | (presumably +1, +2) |

**Service-timer edits**, as stepped at the panel: UP opens the field showing the current days
(FC 20 / 30, UV 56, H2O 30), the next UP steps it — **FC and UV in 10-day steps** (20 → 30 → 40;
UV 56 snapped up to 60), **H2O by 30** (30 → 60) — and Pump 1 saves and returns to the screen.
The saves read back: `f3` 238 → 228 → 210 (= 250 ^ 30, 250 ^ 40) and `f12` 30 → 60, which
confirms both decodes.

**How the bridge uses it:** it learns the UV days whenever nobody has pressed anything for
8 s (the panel times out home in ~5 s) and decodes every state with them; a UV save re-keys
the display at once, and the driver reads that save's frame under the new key.

⚠️ **The UV days are one byte here.** The manual allows up to 365; above 255 the key could not
hold it, so the bridge refuses UV above 250 until a tub has been seen there.

## 4. As-built — what reaches MQTT

Published by `read_spa_data()` in `app.py`:

| Exposed | Entity |
| --- | --- |
| current temp, set point, heat action | `climate` |
| pump 1, pump 2 | 2 × `switch` |
| manual circ (the ClearRay / circ button) | `switch` |
| circ pump | `binary_sensor` |
| light on/off, brightness, effect | `light` |
| spa clock, spa date, heat mode, `temp2`, circ mode, auto / manual circ, panel screen, light cycle time, raw `f3` / `f9` / `f12` | `sensor` / `binary_sensor`, the raw fields as disabled-by-default diagnostics |

**Published but invisible to HA:** `rgb_state_topic` is published every cycle, but
`payload['rgb_state_topic']` is commented out in the discovery config, so HA never binds it.

**Settable:** set point, pump 1, pump 2, "manual circ" (really the ClearRay/circ button),
light on/off, brightness, colour — and, through the panel menus (§3.11), **the spa date and
time** (`text` entities "Set Spa Date" / "Set Spa Time", and a "Sync Spa Clock" button using
`SPA_TZ`), and **the FC / UV / H2O days** (`number` entities, §3.12 — off-grid values land on
the nearest step and say so). "Panel Menu Status" reports progress, refusals and the read-back.

**Heat mode is read, not set.** HA shows it as the "Heat Mode" sensor (AUTO / ECO / DAY). It has
no control, because the HEAT edit has never been recorded (§3.11 *Heat mode*); the library's
`set_heat_mode` refuses every change. It is not offered as a climate `preset_mode`: HA's MQTT
climate needs `preset_mode_command_topic` together with `preset_modes`, so a preset would be
a control that refuses every pick. When the edit is captured and driven, `preset_mode` on the
climate entity is the place for it (`modes = ["heat"]` stays).

**The time can be set to any hour, across noon and midnight.** The driver steps the hour up
through 12 (0 in the field), keys it by the minute held when the edit opened, and sets the AM/PM
field (§3.11). The offline test `tools/test_menu.py` covers all 24 × 24 start and target hours
and a clock tick mid-edit.

Setting the clock across noon and across midnight is not yet confirmed on a live spa. The
simulated panel takes its AM/PM and wrap behaviour from the saved panel captures. Stepping the
hour DOWN through 12 has not been seen, so the driver only steps it up.

**Set-point and pump presses are held while the panel shows a menu**: on a
menu screen the temperature buttons edit the setting and Pump 1 accepts it.

**Panel locks apply to the bridge's presses too, on the 680** (GalKar83's finding,
[pete-jorgensen/SundanceHotTub2MQTT#10](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/pull/10)).
With Temperature Lock on, a set point sent from the bus was never accepted. With Access Lock on,
the light did not change. ⚠️ **Not tested on the 780.** If the 780 does the same, a set point sent
under a Temperature Lock will look like a command that never confirms. The bridge does not read,
set or release the locks (§7, f1 b3 / f10 b4 / f14, and 0xCC 209).

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

## 5b. Observed button-press frames — primary data from the correlation sessions

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

✅ **Identified (§3.11):** the bridge logs a press as `buttondata ^ 1, buttondata`,
so "228 → 229" / "229 → 228" are presses of **Pump 1 (228)** and **Pump 2 (229)**. Pump 1 is also
the menu's accept key. The left-hand number is `a ^ b ^ 1` and the right-hand one `a ^ b`, which is
the code: the first two frames are Pump 2 (229), the last two Pump 1 (228) (§7).

## 6. Open questions — hypotheses, not facts

### 6.1 What separates `f1.b5` from `f1.b6`?
The circulation model is otherwise resolved (§3.1). These two remain: both assert in steady
circulation and differ only across transitions. Likely a pump-speed or settling indicator. They
are published as diagnostics, so the HA recorder will accumulate the transitions needed.
GalKar83's 680 reading — b5 "commanded", leading b6 "running" by about a second — fits the one
circulation start in the saved captures (b7, then b5, then b6, within one second); b5 also clears
while circulation runs on whenever pump 1 starts (3 of 3). What would settle it is in §7.1.

### 6.1b Where does the UV countdown end?
There is no alert countdown in `f13`; the daily countdown is the UV days (§3.12), and its low
3 bits go 0 to 7 on an 8-day cycle (§3.2). That wrap is the 3-bit mask, not a stop or a latch of the UV countdown. Not settled: whether the UV countdown stops at 0,
wraps, or latches, and where a flow / high-limit / water-change alert shows on the wire. The
second needs a tub in a different alert state, which is not worth provoking.

### 6.2 Are `f3`, `f9`, `f12` the service counters?
**Mostly answered (§3.11, §3.12):** `f9` is the **year**, not a counter; `f12` reads the H2O
days and `f3` the FC days XOR 250, confirmed by edits at the panel.

**Daily countdown ✅ (from Home Assistant's recorder history, over 18 days).** All three timers
(the water, filter and UV change days) fall by exactly 1 once per day, in step, at the spa
clock's midnight. No day was skipped, and bridge restarts and `unavailable` gaps did not change
a value. The step moved about 17 s earlier each day, so the spa clock gains about 17 s a day.

They are constant over 48 h but differ between the correlation sessions and the three-day log:
`f3` 241 → **250**, `f9` 23 → **14**, `f12` 2 → **0**, so they move on a scale of
weeks-to-months, as service counters and a year would.

### 6.3 What are `f2` bits 0–2? ✅ The day of the week, Sunday = 0
**Answered.** Hypothesis from GalKar83's Sundance 680 component ([pete-jorgensen/SundanceHotTub2MQTT#8](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/8)), tested on every saved 780 frame with a known spa date: **424 of 424 match**, across five
spa dates and four different weekdays (`python3 tools/weekday_f2.py <logs>`) —

| capture | spa date (`f7`, `f9`) | `f2 & 7` | frames |
| --- | --- | --- | --- |
| correlation sessions | Fri 13 Jan 2023 | 5 | 85 |
| panel walk, before the date was set | Thu 3 Apr 2014, then 2025 mid-edit (also a Thursday) | 4 | 59 |
| same, after the date was saved | Sun 25 Oct 2026 | 0 | 27 |
| service-timer edits | Sun 25 Oct 2026 | 0 | 106 |
| light and clock test | Mon 26 Oct 2026 | 1 | 147 |

The decisive frame is the date save in the panel walk: `f2` went 36 → 0 (low bits 4 → 0) **in the
same frame** as `f7` 28 → 194 and `f9` 25 → 26, so the weekday follows the date that is set, not a
separate counter. Each `f7` has two date candidates (§3.3); in every case only the true date's
weekday matched. The three-day log's values 0, 5, 6 fit too: the spa believed it was 17–19 Jan
2014, Friday to Sunday (those frames are not saved, so they are not counted above). The bits
vary **independently of** circulation and both pumps.

### 6.4 Does `f1.b5` mean "circ mode" vs `f1.b6` "circ running"?
See §3.1. Distinguishable by watching a circulation start/stop edge closely.

### 6.6 What the rest of the family says we have not seen
Sources: Jacuzzi's plaintext variant as documented in
dhmsjs/pyjacuzzi, and Balboa's own Wi-Fi protocol (ccutrer/balboa_worldwide_app), both on
GitHub.

**Buttons — a pattern, with a circularity to avoid.** On this bus a press is **0xE0 + the
Balboa/Jacuzzi button code**: Pump 1 228 = 0xE0+0x04, Pump 2 229 = +0x05, Light 241 = +0x11,
Light 2 / colour 242 = +0x12. ⚠️ Those four are the only *independent* matches. pyjacuzzi
also lists NothingPressed, TempUp, TempDown, ClearRay and Menu, but marks them as inferred, so
they do not confirm this bus's codes.
The pattern predicts, untested: Pump 3 230 (0xE6), Blower 236 (0xEC), Mister 238 (0xEE),
Aux 1/2 246/247. TempRange 0x50 and HeatMode 0x51 do not fit under 0xE0+, so their encoding
here is unknown. ⛔ Predictions, not observations — §3.6's rule stands: send a code only after
seeing a real panel press it.

**Request/response messages a listener never sees.** In both relatives much of the state only
crosses the bus when a client asks: Jacuzzi's panel request (0x19) returns primary filtration
(0x1B), **secondary filter Holiday/Light/Heavy (0x1C)** — the SF screen here read "HOL" —
pump config (0x1D), setup parameters (0x1E), system information / firmware (0x24), filter
cycles (0x27), device configuration (0x2E), module ID (0x94); Balboa adds a fault log (0x28).
They also have direct setters — set time (0x18 Jacuzzi, 0x21 Balboa), save filter cycles,
reset/snooze the ClearRay and water timers — which would beat walking menus. **Nothing on this
bus ever asks**: the factory panel only sends button presses, so the Sundance equivalents (if
the encrypted family has them, presumably in the 0xC_ range like C4/CA/CC) have never been
seen on this bus, and the bridge sends no request frames.

**Fields the status frame probably still holds.** Jacuzzi's periodic PanelUpdate (0x16, the counterpart of our 0xC4)
carries an **error code**, **lock bits**, **12/24-hour and °F/°C flags**, spa and heat
state, filter-2 mode, UV / blower state and **two-byte service timers**. Unmapped here: `f2`
bits 0–2, `f4`'s constant bits, the rest of `f10`, `f14` bit 7 — candidates for those. And the
two-byte timers fit a UV interval up to 365 that the one-byte display key cannot hold (§3.12).

### 6.5 Is there a fault/error surface at all?
Nothing in the decode covers error or diagnostic codes (flow switch, high limit, dry-fire, sensor
fault). A tub with a fault shows it on the panel, so the code exists somewhere on the wire —
most likely in `f13` or a `f2` bit. Nothing has been captured with the tub in a fault state, and
provoking one deliberately is not proposed.

### 6.7 Can the UV days exceed 255, keyed by the low byte? (GalKar83's 680, untested here)
§3.12 limits UV to one byte because no tub here has been seen above 132. GalKar83's 680 component
([pete-jorgensen/SundanceHotTub2MQTT#8](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/8) and its README §9) reports a ClearRay counter of 320–334 days with the
display key equal to its **low byte**: `f13` on the home screen = `UV days & 0xFF`. If that holds on
the 780, the bridge's refusal above 250 could become "learn the high byte from the UV screen".
Untested here; it needs a UV setting above 255 on this tub.

---

## 7. Cross-model field map: 780, 680, Montclair

One table, one row per field (or bit), one column per board. **Only what has been checked on that
board is written in its column**; everything else is `unknown`. Nothing is copied from one board to
another. Statuses: ✅ confirmed on that board; 🟡 hypothesis (a few observations); ❓ unknown or never
seen. Open questions stay as hypotheses (🟡/❓) until a capture confirms them.

The fold and checksum are the same on every board seen so far (`a ^ b ^ 1` per pair; CRC-8 poly
0x07, preload 0xB5, final XOR 0x02), so the field numbers below are post-fold.

Boards and sources:

- **780** — this repository: the cells below come from this document (§2, §3), checked against
  the saved 780 captures (`python3 tools/cross_model_check.py <files>`). Where a cell gives a
  count, it is from those captures.
- **680** — Sundance 680 series, Peyton 2020, controller P/N 6600-158, board PN 24168_C. Source:
  [GalKar83/sundance-rs485-controller](https://github.com/GalKar83/sundance-rs485-controller), README
  §5–§9; checked on the spa, dates in brackets.
- **Montclair** — only the fields decodable from the three frames in [pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5) (one capture); the rest
  is `unknown`.

| Field | 780 | 680 | Montclair |
|---|---|---|---|
| **f0** | hour `^ 6` ✅ (per §2) | hour `^ 6` ✅ | hour `^ 6` ✅ ([pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5), one capture) |
| **f1** b2 | pump 2 ✅ (per §2) | Jet 2 running ✅ | unknown |
| **f1** b3 | no test possible ❓ — clear in all 339 frames of the three later panel captures; no lock was set in them. GalKar83's report (sundance-rs485-controller README §6) gives Access Lock; it needs a 780 capture with the lock on (§7 open captures) | **Access Lock** active ✅ (2026-10); set from the ESP by the LOCK menu walk (confirmed by this bit) and released by one 0xCC 209 ✅ (2026-11-08) | unknown |
| **f1** b5 | moves with circulation, unnamed 🟡 (per §6.1). At the one circulation start captured it set after b7 and before b6, all within one second (1 of 1); it also clears while circulation keeps running when pump 1 starts (3 of 3) and came back about 5 min after pump 1 stopped (1 seen) | Jet 1 low speed **commanded**; leads b6 by ~1 s 🟡. Also cleared by a Jets 1 press during the automatic circulation (f1 96 → 64) and back about 5 min later (1 seen, 2026-11-08), as on the 780 | unknown |
| **f1** b6 | lags circulation, unnamed 🟡 (per §6.1) | Jet 1 low speed **running**, any reason ✅ | unknown |
| **f1** b7 | manual circulation ✅ (per §3.1) | manual circulation (Jet 1 low started by the circulation button) ✅ | unknown |
| **f1** b0, b1, b4 | unknown | never set ❓ | unknown |
| **f2** b0–b2 | weekday, Sunday = 0 ✅ (424 frames, §6.3) | weekday, Sunday = 0 ✅ (22–25 Oct 2026, power-up date; Saturday 7 Nov = 6; with the date set to Wednesday 8 Jul 2026 = 3) | unknown |
| **f2** b3 | unknown | Jet 1 low speed (pump side) ✅ | unknown |
| **f2** b4 | pump 1 ✅ (per §2) | Jet 1 high speed ✅ | unknown |
| **f2** b5 | a value is being **edited**, the set point included ✅ (18 of 18 rises came as `f13` entered an edit state, four captures); a set point change held it for 8 s (1 seen), as on the 680. Agrees with GalKar83's report (sundance-rs485-controller README §6) on the three later panel captures: 16 rises, each with `f13` entering an edit screen, and each fall as the edit closes (for example `f2` 36 → 4 with `f15` 20 → 0 as an edit closes; five more falls in the panel walk and the service-timer edits likewise) | a menu value is being **edited** ✅ (2026-10); set for about 8 s after each set point change (2026-11-08) | unknown |
| **f2** b6 | unknown | never set ❓ | unknown |
| **f2** b7 | clear in all 424 saved frames (85 from the earlier correlation sessions, 339 from the three later panel captures), including 23 with the year at 2014 ❓. GalKar83's report (sundance-rs485-controller README §6) says set while Water Refresh is Off. Not testable here: `f12` = 0 in 27 of 86 panel-walk frames in the saved panel captures (before the H2O screen was visited) with b7 clear in all 27. So on the 780 either 0 meant "due" and not Off, or b7 does not exist. A capture with H2O saved as Off would decide it | set while **Water Refresh is Off**, and after a power loss until the clock is set ✅ | unknown |
| **f3** | Filter Change days, `f3 ^ 250`; 250 = 0 ✅ (menu walk, §3.11) | Filter Change days, `f3 ^ 250`; raw 250 means Off **or** due ✅ | unknown |
| **f4** | b6 = circulation pump running (either mode); rest constant `0b101` ✅ (per §3.1) | constant 6; no separate circulation pump on this model 🟡 | unknown |
| **f5** | water temperature `^ 2`, °F ✅ (per §2) | water temperature `^ 2`; half °C in °C mode, whole °F in °F mode ✅ | `^ 2`, half °C (×2) ✅ ([pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5)) |
| **f6** | heat mode 32 Auto, 34 Eco, 36 Day ✅ (per §2) | 32 Auto, 34 Eco, 36 Day ✅ (16 for under 1 s at power-up) | unknown |
| **f7** | `(day << 3) XOR month` ✅ (§3.3) | `(day << 3) XOR month` ✅ (8 Jul 2026 set on the spa, 2026-11-07); ambiguous, e.g. 22 Oct and 23 Feb both 186 | unknown |
| **f8** | set point, °F ✅ (per §2) | set point; half °C or whole °F ✅ | half °C (×2) ✅ ([pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5)) |
| **f9** | year − 2000, **no XOR** ✅ (§3.3) | year − 2000 **XOR 1** ✅ (2026; power-up default reads 2014) | year − 2000, no XOR ✅ ([pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5): 26) |
| **f10** b4 | no test possible ❓ — clear in all 339 frames of the three later panel captures; no lock was set in them. GalKar83's report (sundance-rs485-controller README §6) gives Temperature Lock; it needs a 780 capture with the lock on | **Temperature Lock** active ✅ (2026-10); set from the ESP by the LOCK menu walk (confirmed by this bit) and released by one 0xCC 209 ✅ (2026-11-08) | unknown |
| **f10** b6 | heater on ✅ (per §2) | heater on ✅ (forced run 2026-11-08: set 5 s after the set point was raised by 1 °C; cleared 4 min 45 s after it came on, with the set point restored after 35 s) | unknown |
| **f10** b7 | automatic circulation ✅ (per §3.1) | scheduled filtration cycle (including the daily cleanup) and the power-up purge ✅ | unknown |
| **f10** b0–b3, b5 | unknown | never set ❓ (candidates: alerts or faults) | unknown |
| **f11** | minute ✅ (per §2) | b0–b6 minute ✅; b7 never set ❓ | minute ✅ ([pete-jorgensen/SundanceHotTub2MQTT#5](https://github.com/pete-jorgensen/SundanceHotTub2MQTT/issues/5)) |
| **f12** | Water Refresh days, plain ✅ (§3.12) | Water Refresh days, plain; 0 = Off or due (f2 b7 tells them apart) ✅ | unknown |
| **f13** | panel state XOR the UV (ClearRay) days ✅ (§3.12) | screen code XOR key; key = (ClearRay days & 0xFF) XOR 78, so the main screen reads as the low byte of the counter ✅ (set on the spa 2026-10-31; counter above 255 checked) | unknown |
| **f14** | temperature-like value V, **XOR 32 while the circulation pump runs** ✅: with b5 undone, `(f5 ^ 2) − f14` is −1 to +3 in 424 of 424 frames (four captures, both circulation states); b5 flipped at the one circulation start captured and at neither pump 1 edge (0 of 2). b6 has been 1 in every frame (only °F seen); b7 a flag, n = 3, meaning unknown (§3.4). GalKar83's report (sundance-rs485-controller README §6) names b1 = Settings Lock, b2 = Service Lock, b6 = °F. Not testable here: the only `f14` changes with b1 or b2 flipped are V dither and pump starts (for example, in the saved light and clock capture, 98 → 68 as the pump starts), and no lock event is in any capture. b6 agrees but is one-sided (set in every frame, never a °C frame) | slow value V (about −1 per hour idle, rises while the pump runs; meaning unknown ❓) XOR flags: b1 flips with the Settings Lock and b2 with the Service Lock (seen as a flip within 5 s of the panel event, 2026-10-25; the absolute lock state cannot be read from f14 alone because V is unknown), b5 flips while the pump runs, b6 = °F ✅; b7 never seen | unknown |
| **f15** | the value of the menu field being edited; 0 otherwise ✅ (§3.11; the hour and minute are keyed by each other) | value shown on the current menu screen; encoding depends on the screen ✅. LANG edit screen, key 117 (2026-11-08): English reads 1 and the Warmer cycle goes 1 → 2 → 3 → 0, so the language code is shifted by an amount that depends on the daily key (rule not found yet) | unknown |
| **frame** | `C4` payload 33 bytes, `CA` 29; the fold reads all but the last, and the last byte is the per-frame mask (the first payload byte XOR 38 in `C4`, XOR 102 in `CA`), no data seen in it ✅ (§3.8). In `CA`, mask ^ (`0x24` + position) leaves plaintext (622 of 622 frames); in `C4` not in that form ❓ | `C4` payload 33 bytes, `CA` 29, the last byte is the per-frame mask: the first payload byte XOR 38 in `C4` (43 of 43 frames), XOR 102 in `CA` (all frames), as on the 780 ✅ (live raw frames, 2026-11-08). `CA` at rest after XOR with the last byte is `66 DA 26 27 28 29 2A 4F 2C 2D 2E D0 30 … 3F`, the 780 pattern. In `C4` the fixed bytes agree with the 780's where both are constant (positions 0, 4, 7, 11, 12, 13, 17; also 3 and 9, which on the 780 are constant only while circulation is off: at the one circulation start rebuilt there, 3 carried f1 b7 and 9 carried f4 b6); the positions that moved in the set point, light, Jets 1 and heater tests were only f1 (2), f2 (5), f8 (16), f10 (21), f11 (22), f13 (26) and f15 (30, 31), so nothing hidden was seen in `C4` ✅ | payload four bytes longer (per [garbled1/pybalboa#122](https://github.com/garbled1/pybalboa/issues/122)) |
| **0xCC** | code = `a ^ b`, **no `^ 1`** (the bridge sends `code, 0`): Up 225, Down 226, Pump 1 228, Pump 2 229, Circulation 239, light 241 / 242, Menu 254, all seen as panel presses ✅; 224 = nothing pressed. 228 started pump 1 (1 of 1), 229 pump 2 (2 of 2). In this form §5b's four frames are 229, 229, 228, 228 | code = `a ^ b`, no `^ 1` (the contributor's ESP firmware log prints `a ^ b ^ 1`, the code with bit 0 flipped): idle 224; Warmer 225, Cooler 226, Jets 1 228, Jets 2 229, Circulation 239, light 241/242, Menu 254, Menu held 209 ✅. A single 209 sent from the ESP's own channel, with no 254 stream before it, releases Access, Temperature, Settings and Service Lock ✅ (2026-11-08); the controller also applies the locks to the ESP's own presses (Temperature Lock: the set point is never accepted; Access Lock: the light does not change) ✅ | unknown |
| **0xCA** | same positions ✅ (§3.5, 621 frames, colours read at the panel): L1 brightness 100 / 66 / 33; L4 mode, 128 = colour fade, fixed colours 2 blue, 7 magenta, 6 red; L2 / L6 / L8 = B / G / R; L9 cycle time, 2 or 4 in fade, 0 on a fixed colour; L0 = 189, L3 = 60, L5 = 255 constant | same positions ✅ (30 Oct 2026 light-cycle test): L1 100 on / 0 off (no brightness steps), L4 mode, L2 = blue, L6 = green, L8 = red, 0–255 per mode (Blue 2: L2 255; Green 3: L6 255; Red 6: L8 255; Violet 7: L2 = L8 = 127); L9 blend speed (2 fast, 4 slow); L0 = 189, L3 = 100, L5 = 255 constant; L7 0 so far ❓ | unknown |

Differences worth noting between models: f9 (the extra XOR on the 680), f4 (the circulation pump
bit, absent on the 680), and the temperature units (half °C on the 680 and the Montclair, whole °F
on the 780 captures). The 680 has no separate circulation pump, one light button with ten modes, and
Jet 1 is a two-speed pump. Bit 5 of f14 flips with whichever pump circulates: the circulation pump on the
780, Jet 1 on the 680. The light frame differs in brightness steps (three on the 780, none on the
680) and in L3 (60 vs 100).

On the 780 the `a ^ b ^ 1` fold is for the `C4` and `CA` payloads; a `0xCC` button code is read as
`a ^ b`. The 680 code list above matches the 780's code for code, and GalKar83's ESP firmware for the 680
reads `a ^ b` as well, so the two boards use one form.

### 7.1 To verify

Each line is a cell above that one board shows and the other cannot yet confirm, and the capture
that would settle it. Nothing here is a reason to remove a cell.

- **f1 b5 / b6 on the 780** — does b5 lead b6 by about a second, and what clears first at a stop?
  A log with sub-second timestamps across an automatic circulation start and stop (the filtration
  window edges, §3.7). The saved logs stamp whole seconds and hold one start and no stop.
- **f14 b5 on the 780 with pump 1 alone** — does it flip for any pump or only for circulation? A
  pump 1 run with circulation off.
- **f14 b6 = °F** — a capture with the 780's panel switched to °C.
- **The locks (f1 b3, f10 b4, f14 b1 / b2)** — set and clear each panel lock on the 780 while the
  bridge logs. *Done on the 680 (2026-11-08): each lock is cleared by one 0xCC 209 and set by a menu
  walk — Menu ×10 to LOCK, Warmer until the option screen (Access → Settings → Temperature → Service),
  Jets 1 saves — and confirmed by f1 b3 / f10 b4 / the f14 flip.*
- **f2 b3 and f2 b7** — f2 b3 needs a two-speed pump (the 780's pump 1 speeds are not captured);
  f2 b7 needs Water Refresh set to Off, and a power cycle.
- **0xCC 209, Menu held** — one long Menu press on the 780. *On the 680 the bus shows Menu 254 for about
  10 s and then one 209; that one frame alone, sent by the ESP, is enough to release a lock.*
- **Montclair** — every Montclair cell rests on one capture; a second one would move them to ✅.

# VDO PQ24 cluster EEPROM dataset map (Generation 3+4 — covers our VQMJ07HH 08.40)

Source of the address map: forum.polo9n.info thread 69327, post by user `-creez-`
(<https://forum.polo9n.info/viewtopic.php?t=69327>). Third-party, unofficial. Everything in
§2 below has been **independently verified against our own cluster's decrypted dataset**
(`eeprom_decrypted.bin`) — see §3.

Addresses are **byte offsets into the DECRYPTED 2048-byte dataset**. `eeprom.bin` in this
folder is the *encrypted* form; `eeprom_decrypted.bin` is the plaintext one.

## 1. Background from the thread
- VDO clusters carry a **93C86** EEPROM (2048 bytes) on the front side under the white light
  mask; readable with a cheap programmer plus a clip or soldered wires.
- **Software 8.4 and 9.0 = "Generation 4"**, and their datasets are **encrypted**. All Gen-4
  clusters share the **same key** and the same dataset structure, so clusters are 1:1
  clonable within the generation. (PQ25 differs — key is unique, stored in the MCU.)
  This validates the whole-generation approach our `vdo_eeprom_codec.py` takes.
- Gen 4 clusters have the extended immobiliser for EDC17 ECUs but stay compatible with all
  other PQ24 ECUs. Clusters < 8.40 are not EDC17-compatible.
- Marelli clusters instead have the EEPROM inside a read-protected MCU (needs Carprog /
  Orange5 / xProg via a 2x3-pin connector); their datasets are not encrypted.
- **The thread contains nothing about needle sweep / Zeigerlauf, the Memory Patch Module,
  or gauge control registers.** It does not answer our open §8.4 question.

## 2. Address map — Generation 3+4 (from 2005)
```
065        speed source: 0x81 = GALA input, 0x83 = CAN
06a-06f    (3 x 2 bytes) unknown
070-08f    key transponder codes (4-byte blocks, 8 slots)
090-0af    1's complement of [070-08f]          <- SELF-CHECK, see §3
0b0-0c6    (3 x 8 bytes) component security (CS is 7 bytes; last byte always 0xff)
0c8-0ca    (6 nibbles) number of learned keys
0cb-0cd    (3 bytes) immobiliser learn status
0cf-0d2    immobiliser on/off switches
10a-10f    (3 x 2 bytes) login code, read directly as decimal
13a-149    (8 x 2 bytes) odometer, 8 redundant copies of ONE 16-bit word.
           Encoding (derived on our cluster, §3): km = ((~word) & 0xFFFF) * 16 + fine
           i.e. the word holds the 1's complement of km/16. The low 0..15 km ("fine")
           are NOT in this field — see §3.
14a-18f    fault memory
1b8-1c0    (3 x 3 bytes) unknown
1ca        checksum over 1cc-1cd and 28e-2ad (see note below)
1cc-1cd    (1 word) Wegstreckenkennzahl (distance constant)
24f        bit1 & bit5 = oil sensor (TOG) mode on/off
251        scale illumination mode (0x20 = with lights, 0x21 = with ignition;
           FIS clusters drive scales/needles/displays separately -> more options)
252        cruise-control lamp mode (0xf9 = only when active, 0xe9 = when switched on)
254        high nibble: 1 = no MFA, 2 = small MFA, 4 = clock only w/ small MFA, 8 = FIS
           low nibble : 1 = clock display, 2 = Ibiza MFA, 3 = standard MFA
255-256    warning-lamp switches
26e-271    black-ice warning hysteresis   (NOTE: EEPROM bytes — unrelated to CPU RAM $026E!)
28e-29d    (8 words) speed values for curve points, km/h, factor 0.0625
29e-2ad    (8 words) speed scale points, DEGREES, factor 0.0625
2ae-2b5    (4 words) rpm values for curve points, 1/min
2b6-2bd    (4 words) rpm scale points, DEGREES, factor 0.0625
2be-2dd    fuel sender resistance curve (paired with the litre curve below)
2de-2fd    fuel content curve in litres, factor 0.0625
           (1st 8 words inverting curve rising/falling as needed; 2nd 8 words same values
            but must rise)
2fe-30d    (8 words) fuel scale points, DEGREES, factor 0.0625
336-341    (6 words) coolant temp curve A, degC, factor 0.125    <- see §3 correction
342-34d    (6 words) coolant temp curve B (hysteresis), degC, factor 0.125
34e-359    (6 words) coolant scale points, DEGREES, factor 0.0625
3ce-3d3    WFS 3.1 switches, relevant for EDC17 immobiliser adaptation
3e4        outside temperature active; 0xff = inactive
486-495    (2 x 4 words) illumination; all 0x00 = off (possibly KL58 PWM duty)
4de        remaining-range (Rest KM) on/off
4df        welcome message: 0x6d = on
```
Generation 2 (before 2005): `263` = MFA mode, `264-265` = warning-lamp switches.

Marelli (for reference only): login `4EC-4ED`/`4F0-4F1`/`4F4-4F5`, CS `400-417`,
VIN `418-43f`, transponders `45c-4bb`, WFS ID `4bc-4eb`, mileage `600-7ef`.

**Checksum note (1ca).** It protects `1cc-1cd` and `28e-2ad` (distance constant + speed
curve). It is inversely proportional to each protected byte with factor 1, plus an offset
that cancels out if you only subtract the *change* made in the protected range. So: if we
ever alter the speed curve, `1ca` must be adjusted by the negated sum of the deltas.
Our sweep patch does not touch those bytes.

## 3. Verification against OUR cluster (2026-08-27)
Run `python3 gaugecal.py curves eeprom_decrypted.bin` to reproduce.

Checks that passed:
- **1's-complement self-check** at `070`/`090`: all 8 bytes of the two *used* transponder
  slots are exact complements of one another (byte-for-byte, `x` against `~x`). The other
  six slots are `0xff` in both halves, i.e. unused — so the rule holds wherever it applies.
- **Key count** `0c8-0ca` = `22 22 22` → 2 keys, matching exactly the two non-empty
  transponder blocks. Independent cross-check of both fields.
- **Login / SKC** `10a-10f` — the same value in all 3 redundant copies, and it matched the
  figure the owner already had for this car. Redundant copies agreeing is itself strong
  evidence of correct alignment. (Value withheld: it is the immobiliser secret.)
- **Odometer** `13a-149` — the same 16-bit value in all 8 copies, and the figure it decodes
  to matched the dash to within 1 km. The algorithm is documented upstream in `reference/Odometer.md` and is *not* a plain
  number: the field is **8 independent 16-bit down-counters**, each initialised to `0xFFFF`
  at the factory and decremented **round-robin, one counter per 2 km**. So

  ```
  km = 2 * SUM(0xFFFF - block_i)  for i = 0..7
  ```

  Worked example: with every block reading `0xF000`, that is
  `2 * 8 * (0xFFFF - 0xF000)` = `2 * 8 * 4095` = 65 520 km.

  128 bits are used to store a 19-bit value — it spreads EEPROM wear and makes random
  corruption detectable, since the counters must always be in the regular staircase pattern.
  The odometer therefore has **2 km resolution in EEPROM**; the cluster keeps the odd km in
  RAM and updates the display every 1 km, which is why a cluster that has lost power always
  comes back showing an even number. Our 1 km discrepancy is exactly that RAM remainder.
  Immediately before the counters sit **7 checksum bytes at `0x133-0x139`**
  (= `d5 69 10 e0 4f b7 26` on our cluster).

  **Consequence: never treat this field as a plain number.** (An earlier revision of this
  document derived a "1's complement of km/16" rule from our dump — that happens to give the
  right answer only because all 8 counters are currently equal, i.e. we are exactly on a
  cycle boundary. It breaks as soon as they differ. Use the sum formula.)
- **Speed source** `065` = `0x83` = CAN, correct for a 9N3.
- Owner-confirmed cluster identity: part number **6Q0920843 A0V06**, matching the
  `6Q0920843` / `VDO V06` that Phase 0 expects.
- **`254` = `0x81`** → high nibble 8 = **FIS**, low nibble 1 = clock display. Confirms this
  is the Full-FIS cluster the project is built around.
- **Every gauge curve comes out monotonic and physically sensible** (see §4).
- Coolant curve A/B differ only in the 4th point (116 vs 108 degC) — the forum author's
  guessed "Hysterese?" is correct.

Correction to the published map: the two coolant degC curves start at **0x336 and 0x342**
(6 words each). The forum's `336-342` / `343-34d` boundaries are off by one and produce
garbage; `0x343` in particular decodes to nonsense.

### 3.1 Why `eeprom.bin` does not decode — our codec is missing a step
`eeprom.bin` (the encrypted dump) does **not** decode to this layout through
`vdo_eeprom_codec.py`; the 070/090 self-check fails everywhere on it. The upstream
`reference/VDO EEPROM Encryption.md` explains why. The encryption has **two** stages:

1. **Address scrambling** — the 10-bit word address is XOR'd with a cluster-type constant,
   then some of its 10 bits are swapped pairwise. The block is stored at that new location.
2. **Data scrambling** — byte 0 XOR (high 2 bits of the original address) XOR a constant;
   byte 1 XOR (low 8 bits of the original address) XOR a constant.

Our codec implements **stage 2 exactly** — including both documented exceptions (the single
unscrambled block at word `0x116`, and the 16 odometer bytes at word `0x9D`, which our codec
handles as the `0x9C..0xA4` window keyed from ROM table `$538F`; note `0x9D * 2 = 0x13A`,
precisely the odometer address in the map above). It implements **none of stage 1**. That is
exactly the observed symptom: bytes descramble correctly (which is why redundant copies come
out equal — 125 adjacent-equal words where the raw data had 0) but every word sits at a
permuted address, so no field lands where the map says.

**Therefore: `vdo_eeprom_codec.py` is incomplete. Do not use it to compute bytes for any
EEPROM write.** Recovering stage 1 means finding the XOR constant and the bit-swap pattern
for our cluster type — tractable, since we now hold both `eeprom.bin` (scrambled) and
`eeprom_decrypted.bin` (plain) for the *same* cluster, which is enough to solve the
permutation directly. Until then, `eeprom_decrypted.bin` is the source of truth for reading.

The 070/090 complement test remains the cheap decisive validator: any correct decode must
pass it on the used key slots.

## 4. THE KEY RESULT — needle angles are stored in 1/16 degree
Every gauge curve maps a physical quantity to a **needle angle**, and the angle unit is
**0.0625 deg = 1/16 deg**. So the number the firmware pushes at a needle is an angle in
sixteenths of a degree. From our own dataset:

| gauge | zero (raw / deg) | full scale (raw / deg) |
|---|---|---|
| Tachometer  | 14 / 0.88 | **4026 / 251.62** |
| Speedometer | 7 / 0.44  | **4138 / 258.62** |
| Coolant     | 67 / 4.19 | **1428 / 89.25**  |
| Fuel        | 48 / 3.00 | **1444 / 90.25**  |

Curves (value -> angle):
- tach: 0 rpm→14, 840→413, 3969→2009, 7880→4026 (essentially linear, ~31 rpm/deg)
- speedo: 1.5 km/h→7, 4.5→158, 46.75→1132, 75.56→1797, 93.5→2019, 170.44→3016,
  247.31→3998, 256.94→4138
- coolant: 30 degC→67, 50→67, 74→737, 116→737, 124→1323, 129.75→1428
  (the flat 74-116 degC → same angle is the familiar VW "sticks at 90" behaviour)
- fuel: 48, 48, 236, 408, 754, 1099, 1444, 1444

**Two consequences for the needle-sweep project:**

1. **We now know what a full sweep is**: ramp each gauge from its zero raw value to its
   full-scale raw value and back — 14→4026 for the tach, 7→4138 for the speedo, 67→1428
   coolant, 48→1444 fuel. No guessing at "a large value".

2. **Phase 1 gets a precise numeric target.** Instead of hunting for "a cell that rises with
   revs", we can predict the exact contents of the tach needle cell at a given rpm and grep
   the RAM snapshot for it:

   | rpm | expected cell value | LE bytes |
   |---|---|---|
   | idle ~840 | 413 (0x019D) | `9D 01` |
   | 2000 | 1005 (0x03ED) | `ED 03` |
   | 3000 | 1515 (0x05EB) | `EB 05` |
   | 4000 | 2025 (0x07E9) | `E9 07` |

   `gaugecal.py find` does this against a `DumpMem` snapshot. Capture at two different rpm
   and intersect the candidate lists — the real needle cell tracks the prediction at every
   rpm, coincidences will not.

3. **Bonus search target:** if the firmware shadows the EEPROM curves into RAM at boot, the
   curve itself is findable as a byte run, and the code that interpolates it (and therefore
   writes the needle cell) is nearby. Search a RAM dump for:
   - tach angle curve:  `0E 00 9D 01 D9 07 BA 0F`
   - speed angle curve: `07 00 9E 00 6C 04 05 07 E3 07 C8 0B 9E 0F 2A 10`
   - temp angle curve:  `43 00 43 00 E1 02 E1 02 2B 05 94 05`
   - fuel angle curve:  `30 00 30 00 EC 00 98 01 F2 02 4B 04 A4 05 A4 05`

## 5. Mechanical note (thread post 13)
The needles are servo/stepper driven with **low torque** — they cannot overcome even light
friction, so a needle pressed too close to the dial will stick. Keep ~0.5 mm gap. VDO has
robust end stops (Marelli's are fragile), and a needle can be re-zeroed by rotating it
anticlockwise against the 0 stop. Relevant if a sweep ever drives a needle to the stop.

## 5. Coding changes made to this cluster (2026-08-27)

Both verified against the thread's own wording before writing, and both read back from the
cluster afterwards. `DumpEeprom`/`LoadEeprom` work on the **decrypted** dataset — confirmed
by comparing a live dump against `eeprom_decrypted.bin`: the coding bytes match exactly and
the only differences are the fields that genuinely move (odometer, fault memory, adaptation
counters). So the codec is not needed for this kind of edit; write plaintext values.

| addr | was | now | forum wording | effect |
|---|---|---|---|---|
| `252` | `E9` | `F9` | *"Modus Tempomatlampe (f9 = nur wenn aktiv; e9 = wenn eingeschaltet)"* | cruise lamp lights only while the speed is actually held, not when the system is merely switched on |
| `4df` | `00` | `6D` | *"Willkommensmeldung bei 0x6d an"* | the cluster's built-in greeting is shown at power-on |

Written as 16-bit aligned words (`$252-$253` and `$4DE-$4DF`), rewriting the neighbouring
byte with its own current value, because the 93C86 is word-organised. `$4DE`, the
remaining-range switch, was preserved as `$E3`.

Neither address falls inside the checksum at `1ca` (which covers only `1cc-1cd` and
`28e-2ad`), and neither has a redundant copy elsewhere in the dataset — checked.

Backup before the change: `ee_before_coding.bin`. To revert:

```
./session.sh run LoadEeprom 0x252 revert_cruise.bin     # E9 00
./session.sh run LoadEeprom 0x4DE revert_welcome.bin    # E3 00
./session.sh run Reset
```

The greeting enabled here is the fixed string in ROM. A *custom* welcome text is not an
EEPROM setting on this cluster — it would mean finding and replacing the string in the ROM
image, within its existing length, and is unrelated to this byte.

## 6. Beyond the article — analysis of the undocumented regions (2026-08-28)

The forum map covers about 800 of the 2048 bytes. This section is what the remaining ~1250
give up to analysis. **Nothing here has been written to the cluster**, and none of it is
verified the way §2 was — it is structure and inference, labelled as such.

### 6.1 Method

Three techniques, because none alone is conclusive:

1. **Coverage** — subtract the documented ranges and see what is left. 1246 bytes are
   undocumented, 1237 of them carrying real data rather than `$00`/`$FF` fill.
2. **Shape** — a monotonic run of 16-bit words is a calibration curve; a byte in the middle
   of a run of similar magnitudes is a coding switch; ASCII is a part number.
3. **Differential** — three full dumps exist, taken at different times: one via a
   programmer (older) and two over KWP on 2026-08-27, half an hour apart. Anything that
   changes is a live counter, not a setting. This is the sharpest tool we have, and it is
   what makes §6.2 more than a guess.

### 6.2 Service intervals — `$1F0-$1FF` (the main find)

Constant in all three dumps, and the values are unmistakable:

| addr | value | reading |
|---|---|---|
| `$1F0`, `$1F2` | 365, 365 | days |
| `$1F4`, `$1F6` | 150, 150 | ×100 km = 15000 km |
| `$1F8` | 365 | days — **fixed interval** |
| `$1FA` | 730 | days — **LongLife** |
| `$1FC` | 150 | 15000 km — fixed interval |
| `$1FE` | 300 | 30000 km — LongLife |

365 d / 15000 km and 730 d / 30000 km are exactly VAG's fixed and LongLife service schemes,
which is too specific a coincidence to be anything else. Two pairs appear, most likely oil
service and inspection, or minimum and maximum.

Immediately after it, `$200-$20D` **changed between the dumps** — that is the live
countdown (days and distance remaining), not configuration. The clean split at `$1FF/$200`
is itself good evidence that the block above is the limits.

The article documents none of this. Normally these are set through VCDS adaptation, and
that remains the safer route; the value of knowing the addresses is being able to read the
current state and to verify what an adaptation actually wrote.

### 6.3 The missing odometer remainder — probably `$133-$139`

§3 left an open question: the odometer at `$13A-$149` stores km/16, and the low 0-15 km are
not in that field. `$133-$139` sits immediately before it and **changes between dumps**.
That is where the remainder almost certainly lives. Not proven, and not worth proving by
experiment — nothing good comes of writing near the odometer.

### 6.4 Two undocumented curve tables

* `$30E-$333`, 20 words, rising to 1144 then falling back to 40. The rise-then-fall shape is
  characteristic of a **fuel sender characteristic**; it sits directly after the documented
  fuel block, so it is plausibly the second sender of a saddle tank or an alternative
  sender variant.
* `$36E-$381`, 10 words, 10/38/63/87/110/133/154/176/198/224 — nearly linear, ~22 per step,
  directly after the coolant scale points. A temperature-ish scale of some kind.

### 6.5 Where more coding switches most likely are — `$4D8-$4E5`

The two switches we changed on 2026-08-27 (`$4DE` Rest-KM, `$4DF` welcome message) sit at
the end of a run of bytes of the same character:

```
$4D8: 9C 7B 76 6B 6B 6B E3 00 14 0A 0A 00 04 0A
           ^^ ^^ ^^ ^^ ^^ ^^ ^^
                           |  +-- $4DF welcome  (00 -> 6D turned it on)
                           +----- $4DE Rest-KM  (E3)
```

`$6B`, `$76`, `$7B`, `$9C`, `$E3` are the same kind of opaque magic values as the documented
`$6D` = "welcome on". This is the most promising place in the dataset to look for further
display and feature switches. `$496-$4BF` nearby is different in character — an
eight-byte record `00 80 BF FF 02 35 9A FF` repeated three times, i.e. a structured table
rather than a switch list.

### 6.6 What is NOT configuration

Undocumented bytes that changed between dumps, and so are live data:
`$05C-$05D`, `$133-$139`, `$1C9`, `$200-$20D`, `$21E-$220`, `$3BC-$3C7`, `$3DA`, `$3DD`.
Leave them alone.

### 6.7 How to identify the rest safely

Do not guess a meaning and write it. Two methods actually work:

1. **Differential.** Change one setting by a known-safe route (VCDS adaptation, or a
   documented byte), dump before and after, and diff. One byte changed, one meaning learned.
   Our three dumps already demonstrate how sharp this is.
2. **Donor dump.** A dataset from another `VQMJ07HH 08.40` cluster with a feature enabled
   that ours lacks. The diff points straight at the byte. Gen-4 clusters share the key and
   the structure (§1), so datasets are directly comparable.

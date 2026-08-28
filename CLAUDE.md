# PROJECT: Needle-sweep (Zeigerlauf) patch for VW Polo 9N3 VDO cluster — from scratch

You are Claude Code running on the user's laptop, connected to a car via an OBD/K-line
serial adapter. Your job is to help the user **develop a power-on needle-sweep for their
specific instrument cluster**, which has no ready-made solution. This is a legitimate
retrofit/coding project on hardware the user owns. Read this whole file before acting.

---

## STATUS 2026-08-28 — REBUILT ON THE CLUSTER'S OWN SERVO. Read this before anything below.

The sweep lives in EEPROM at `$552-$657` (262 bytes, code 230). **The tachometer and the
speedometer** run to full scale and back in **3.0 s** on every wake; coolant and fuel do not
move at all. Verified from a genuine `Reset`: climb 0.81 s, turn at 1.29 s, done at 3.00 s.

**The protocol in this file, Phases 0-4, is history** — it records how we got here and is
still worth reading for the reasoning, but do not re-run it. The current picture is
`PATCH_ENGINEERING.md` §17 and `FIELD_CHEATSHEET.md`; §15-§16 describe the superseded build
that drew the motion itself.

Things below that are now known to be wrong or superseded:

* **The port is the FTDI serial number (e.g. `AB0CD1EF`), never `/dev/cu.usbserial-...`.** .NET's
  `SerialPort.BreakState` does not drive the K-line on macOS. `libftd2xx.dylib` sits next
  to the binary; `session.sh port` returns the right form.
* **`Reset` is how you reboot the cluster, not the ignition key.** It runs on permanent
  power, so KL15 off/on leaves the CPU running and RAM intact, and a freshly written patch
  stays inert. A long sleep (a minute or more) does reset it.
* **`DumpEeprom`/`LoadEeprom` work on the DECRYPTED dataset.** Write plaintext values;
  `vdo_eeprom_codec.py` is not needed for coding edits. Verified by comparing a live dump
  against `eeprom_decrypted.bin`.
* **`kw1281test` in this folder is our own build** (upstream's `WriteRAM` ignored its VALUE
  argument; we also added `WriteRamBlock` and `WriteRamPairs`). Stock is `kw1281test.orig`.
  The cluster silently ignores block writes longer than **16 bytes** — always read back.
* Coding changes made on 2026-08-27: `$252` = `$F9` (cruise lamp only while the speed is
  actually held) and `$4DF` = `$6D` (greeting on). See `EEPROM_MAP.md` §5.

Undo, if ever needed — `restore_block.bin` (original factory patch block),
`revert_cruise.bin`, `revert_welcome.bin`, then `Reset`. Backups: `ClusterBackup_*.bin`,
`ee_before_coding.bin`.

The safety rules in the next section still apply in full.

## Hard rules (safety — never break these)
1. **Never run `WriteEeprom` or `LoadEeprom` during exploration.** The EEPROM is the
   odometer/immobiliser/adaptation store; a bad write can brick the cluster. All
   experimentation uses **`WriteRAM` only**, which is volatile and fully recovered by an
   ignition power-cycle. EEPROM writes happen only at the very end, once a change is
   fully validated, and only with explicit user confirmation.
2. **WriteRAM experiments only while the car is stationary**, in Park/neutral with the
   parking brake set, engine off or idling, well ventilated. Poking cluster RAM can reset
   the cluster or move gauges / trip warning lights — never acceptable while driving.
3. **One change at a time. Log every command and its output.** After any `WriteRAM`, know
   how to undo it (write the old value back) or power-cycle.
4. If the cluster stops responding, tell the user to **cycle the ignition** (KL15 off/on)
   and re-establish comms. Nothing done via WriteRAM survives that.
5. Change exactly one variable in the car's state per step (rpm OR a write), so cause and
   effect stay unambiguous.

## Files in this folder
- `*bank0*.bin … *bank4*.bin` — the cluster's NEC ROM, by bank. Memory map below.
- `eeprom_original.bin` (or the user's 2048-byte dump) — the cluster's external EEPROM.
- `decoded.bin` — the EEPROM de-obfuscated (see codec). Regenerate with the codec tool.
- `vdo_toolkit.py` — ROM disassembler / xref / byte-search. (`dis BANK START END`,
  `xref ADDR`, `find HEX`). Addresses are CPU addresses in hex.
- `vdo_eeprom_codec.py` — verified EEPROM de/obfuscation (`decode`, `encode`,
  `wordraw i value`). Needs `*bank0*.bin` in this folder.
- `ramdiff.py` — diff two/more `DumpMem` snapshots to find cells that track rpm/speed.
- `eeprom_decrypted.bin` — the cluster's EEPROM dataset in PLAINTEXT (verified). Use this,
  not `eeprom.bin`, for reading parameters.
- `EEPROM_MAP.md` — the dataset address map (verified against our own cluster) + the key
  finding that needle positions are angles in 1/16 degree.
- `gaugecal.py` — reads the gauge scale curves and predicts what a needle cell must contain
  at a given rpm/speed/temp; `find` greps a DumpMem snapshot for it. (`curves`,
  `predict`, `find`).
- **`TEST_PLAN.md` — the staged plan with go/no-go gates. START HERE at the car.**
- `reference/` — the upstream gmenounos/vwcluster patches and docs. None match our ROM;
  they are for reading and porting, never for installing.
- `kw1281test` (the program) — the diagnostic tool you drive the car with.

## The tool: kw1281test command syntax
Invocation: `kw1281test <PORT> <BAUD> <ADDR> <COMMAND> [args]`
- PORT: the user's serial port (ask; their last log used `COM5`).
- BAUD: `10400`. ADDR: `17` (instrument cluster). Numbers accept hex `0x...` or decimal.

Commands you will use (all confirmed against this build):
- `ReadIdent` — identify cluster. Expect `6Q0920843 … VDO V06`, coding `01144`.
- `ReadSoftwareVersion` — expect `VQMJ07HH 08.40`. Unlock code for this version is known
  to the tool (`34 3F 43 39`); it also does seed/key automatically.
- `ReadROM 0xADDR` — read 1 byte of NEC ROM (live).
- `ReadRAM 0xADDR` — read 1 byte of live RAM. Prints `Address N ($XXXX): Value V ($VV)`.
- `DumpMem 0xSTART 0xLEN file.bin` — read a live CPU-memory range to a file (uses the
  cluster's read-memory custom command, 15 bytes/block). **This is our snapshot tool.**
- `WriteRAM 0xADDR 0xVAL` — write 1 byte of live RAM (volatile).
- `ActuatorTest` — interactive output test; `N` = next, `Q` = quit. Moves gauges/lamps to
  fixed test states. Cannot be run at the same time as RAM reads (single channel).
- `Reset` — soft-reset the cluster.

Only ONE of {ActuatorTest, ReadRAM/DumpMem} can talk on the bus at a time. Plan around it.

## What we already know about THIS ROM (verified — don't re-derive unless needed)
CPU: 65C02 core. Memory map:
- `$0000–$1FFF` RAM + memory-mapped I/O (I/O around `$1F00`; working RAM in `$00xx` zero
  page and `$02xx–$0Fxx`; more flags in `$14xx–$15xx`).
- `$2000–$7FFF` fixed ROM = **bank0** (file offset = addr − 0x2000).
- `$8000–$FFFF` paged ROM = **bank1..4**, selected by writing `$1F0A` (bank1 is the boot
  bank; 2–4 are overlays). Also `$1F0F` is an ISR/context page register.
When disassembling `$8000–$FFFF`, specify which bank; addresses overlap between banks.

EEPROM obfuscation (codec, verified by roundtrip + redundant-copy check):
- 1024 little-endian 16-bit words. `raw = value XOR key(index)`.
  `key_lo=(i&0xFF)^0xC5`, `key_hi=((i>>8)&3)^0xC5`; special window `i=0x9C..0xA4`
  uses ROM table `$538F` XOR `0x53`; word `i=0x116` is stored raw.
- Low-level driver: 3-wire bit-bang on port `$1FAC` (`$FC43` read / `$FC7A` write);
  storage manager bank2 `$CAB0–$CCC1`; per-word transform `$CDC0`.
- Store is a wear-leveled ring log with redundant copies — do not hand-edit live slots.

Actuator-test engine (this is our lead into moving the needles):
- Test index counter: RAM `$0711`. Per-test dispatch via `JMP ($BDD6,X)` (table at
  `$BDD6`, bank1). Test code table at `$BE15`; per-index code byte stored to `$0715`.
- Gauge-select working vars during the test: `$0713`, `$0714` (set by the per-test
  routines at `$BE9D`/`$BEA4`/`$BEB0`…). These select which output is driven; the actual
  needle deflection value is produced downstream — we still need to pin that cell (see
  Phase 2). Disassemble `bank1 $BE3C–$BF20` for the engine and `$BE9D+` for per-gauge acts.
- Feature/coding bits live in RAM `$0412–$0415` (each bit decoded individually in bank1
  `$A258–$A43D`). Reference only; not the sweep.

Reality check on approach: needle sweep is NOT available via VCDS/adaptation on 9N3, BUT
this SoC (CDC16xxF-E) has a built-in **Memory Patch Module**: at boot the ROM loads a patch
block from EEPROM and redirects ROM code into RAM-resident patch code. This is the sanctioned
"hook" — a permanent EEPROM-loaded sweep IS possible. Our loader is confirmed at bank2 $EFAE.
See PATCH_ENGINEERING.md for the full dossier. Key confirmed facts for this ROM:
- Patch EEPROM byte address ~ **$552** (verify by DumpEeprom first).
- Patch code copied to RAM base **$0EBE** (buffer end $14F4).
- Patch-module regs: $1E64 addr-lo, $1E65 addr-mid, $1E66 addr-hi, $1E67 data, $1E68 PER0,
  $1E69 PER1. Version match bytes H4=$40, H5=$08 (or $BD).
- ~~Gauge control registers driven by the sweep are **$026C-$0273**~~ — **RETRACTED
  2026-08-27.** That held for the *reference* ROM (VWK501MH). Static disassembly of OUR ROM
  shows $026C-$0273 is a 17-bit elapsed-time counter + wrap-around deadline + flags, not a
  gauge. See PATCH_ENGINEERING.md §8.1 for the evidence. The user's "these change with
  revs" observation does not discriminate — a free-running counter always changes.
- **The gauge chain, settled 2026-08-27 (PATCH_ENGINEERING.md §14-§15):**
  | address | role |
  |---|---|
  | `$0262-$0265` | four single-byte per-gauge status fields |
  | `$0266` / `$0268` / `$026A` / `$026C` | coolant / tach / speedo / fuel **angle** in 1/16 degree |
  | `$00D4` / `$00DF` / `$00EA` / `$00F5` | coolant / tach / speedo / fuel **servo struct**, 11 bytes each |

  The `$026x` cells are the *source*, not the control: bank0 `$5BE3` loads a gauge's demand
  as `mirror + $15`, so **motor steps = angle + 21**, but only when that gauge's state
  handler asks for a reload. Writing them alone moves nothing for many seconds. The struct
  is demand at +0/+1, then two cascaded 1/32 lag stages at +2 and +5 (fraction, then
  16-bit integer); +6/+7 is the commanded needle position, +8 the velocity, +$0A the mode.
  To drive a needle, write the demand and both stages every pass and leave +8 alone.
Still to resolve before an EEPROM install: the RAM test of the finished patch at the car
(`python3 armpatch.py` prints the sequence).

## Live protocol — run this in order, pausing for the user each step

### Phase 0 — Comms & identity (2 min)
1. Ask the user for the serial PORT. Establish comms:
   `kw1281test <PORT> 10400 17 ReadIdent` then `... ReadSoftwareVersion`.
   Confirm `6Q0920843` / `VQMJ07HH 08.40`. If different, STOP and report — our ROM
   analysis may not apply.
2. Verify the ROM in the car matches our files (so our static addresses are valid):
   `kw1281test <PORT> 10400 17 ReadROM 0x2089` (expect `AD` = $AD),
   `... ReadROM 0x208A` (expect `B4`), `... ReadROM 0x208B` (expect `FF`).
   (These are the first bytes of the reset routine in bank0.) If they match, proceed.

### Phase 1 — Find the gauge/telemetry cells by rev-correlation (uses the user revving)
Goal: locate the RAM cells that track engine RPM (tach chain) and confirm speed cell is 0
(parked). We diff DumpMem snapshots.
1. Engine OFF, KL15 ON:  `DumpMem 0x0000 0x1000 ram_A_off.bin`
2. Engine idling:        `DumpMem 0x0000 0x1000 ram_B_idle.bin`
3. Ask the user to hold ~3000 rpm steady, then: `DumpMem 0x0000 0x1000 ram_C_3000.bin`
   (If holding revs steady is hard, capture at 2000 and 4000 too — more points help.)
4. `python3 ramdiff.py 0x0000 ram_B_idle.bin ram_C_3000.bin`
   Focus on 16-bit LE words that rise monotonically with rpm — those are tach-chain
   candidates (raw rpm and/or smoothed needle target).
5. **Then use the numeric prediction — this is the fast path.** From our own EEPROM we know
   the tach needle cell holds an angle in 1/16 degree, and exactly what it must contain at a
   given rpm (see EEPROM_MAP.md §4):
   ```
   python3 gaugecal.py find eeprom_decrypted.bin rpm 840  ram_B_idle.bin  0x0000
   python3 gaugecal.py find eeprom_decrypted.bin rpm 3000 ram_C_3000.bin 0x0000
   ```
   Expected values: ~413 (`9D 01`) at idle, ~1515 (`EB 05`) at 3000 rpm. **Intersect the two
   candidate lists** — the real needle cell matches the prediction at every rpm; coincidences
   will not survive two different rpm points. Add a third capture if the intersection is >1.
6. Also dump `0x1400 0x0200` (flags region) across the same states if the $0x00xx range
   is inconclusive.
Deliverable of Phase 1: the tach needle cell address (and rpm-tracking candidates).

### Phase 2 — Find & confirm the needle control registers
**Read PATCH_ENGINEERING.md §8 first.** The old "big shortcut" ($026C-$0273 from the
reference patch) is RETRACTED — on our ROM those cells are a timer, not a gauge (§8.1).

2a. Sanity check only (~30 s, safe, volatile, don't overspend on it): engine OFF, KL15 ON,
  `WriteRAM 0x026E 0xCF` `WriteRAM 0x026F 0x0F` `WriteRAM 0x0270 0xF1` `WriteRAM 0x0271 0x0F`
  Ask what the tach did. Expect **no needle movement** (this perturbs a timeout, and an
  ignition cycle clears it). If a needle DOES move, the static analysis is wrong — say so,
  re-read §8.1, and treat $026E-$0271 as the target after all.

2b. The real path — drive the Phase-1 candidates:
Goal: find the cell(s) that, when written, physically move a needle; identify any
"override/test-active" flag that stops the normal loop from fighting us.
**Write angles, not arbitrary values** — the unit is 1/16 degree (EEPROM_MAP.md §4). For the
tach, 14 = rest, 2009 = ~4000 rpm position, 4026 = full scale. So a mid-scale test is
`WriteRAM <lo> 0xD9` / `WriteRAM <hi> 0x07` (2009). Full-scale/zero values per gauge:
tach 14..4026, speedo 7..4138, coolant 67..1428, fuel 48..1444.
1. Prefer discovery with **engine OFF, KL15 ON** so control loops are quiescent and the
   needle rests at 0 (a jump is unambiguous). Pick a Phase-1 tach candidate `A`.
2. `WriteRAM 0xA 0x40` … watch the tachometer needle. Escalate `0x80`, `0xC0`, `0xFF`.
   Ask the user to report needle movement after each write.
   - If the needle jumps and holds → `A` is (part of) the tach target. 
   - If it flickers then falls back → the loop overwrites it; note it, and look for an
     override flag: re-scan Phase-1 changed bytes for a bit that the actuator engine sets
     (cross-ref `$0711/$0713/$0714` neighbourhood via `vdo_toolkit.py xref`). Set that
     flag, then retry the position write.
   - If nothing moves → try the next candidate.
3. Repeat to map all four gauges (tach, speedo, coolant, fuel). Speedo target may be a
   separate 16-bit cell; coolant/fuel are slower steppers.
4. Record: for each gauge, the position cell(s), the value→deflection relationship
   (min/mid/max), and any override flag required.
Deliverable of Phase 2: a table {gauge → position address, override flag, max value}.

### Phase 3 — Host-driven sweep proof of concept (the milestone)
Write a short script (bash calling kw1281test, or reuse the serial session) that, with the
override set if needed, ramps each gauge 0→max→0 with ~20–40 ms steps, sequentially or
together, to reproduce the classic sweep — entirely from the laptop, engine off.
- Confirm visually with the user. This proves we can command the sweep and gives the exact
  motion profile the permanent patch must reproduce.
- Nothing here touches EEPROM. Fully recoverable.

### Phase 4 — Build & install the EEPROM sweep patch (the real deliverable)
Only after Phase 3 proves the motion. Follow PATCH_ENGINEERING.md:
1. `DumpEeprom 0 2048 backup.bin` and KEEP IT. Inspect $552/$4F4/$4F6 to confirm the patch
   region and that it is currently empty.
2. Find our hook point (gauge/main-loop intercept) + restore vector, and the CPU->physical
   mapping for the data block (see dossier section 5).
3. Author the sweep code at RAM base $0EBE using the gauge registers **as established in
   Phase 2** (NOT $026C-$0273 — see PATCH_ENGINEERING.md §8.1); assemble; build the 6-byte
   header (H4=$40, H5=$08 or $BD; H0 checksum per dossier); append the data block + PER
   trailer.
4. `LoadEeprom 0x552 our_patch.bin` -> `Reset` -> ignition off 30s -> on. Watch for sweep.
5. If it does not boot, restore backup.bin. Never install a block whose H4/H5 mismatch.
Do Phase 4 as a separate, reviewed session — not mixed with discovery.

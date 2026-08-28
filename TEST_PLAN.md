# Needle sweep — test plan

Target: VW Polo 9N3, VDO cluster **6Q0920843 A0V06**, ROM **VQMJ07HH 08.40**.
Goal: a permanent power-on needle sweep, installed as an EEPROM patch block.

Read `PATCH_ENGINEERING.md` §8-§9 before running this. The short version of where we stand:

* The patch mechanism, format and install procedure are **fully understood and verified**
  (§9.1) — our parser reproduces all five reference patches' checksums byte-for-byte.
* Our hook point is **found**: `$6584` in the main loop (§9.5).
* The value a needle takes is a **16-bit angle in 1/16 degree**, confirmed twice over (§9.3).
  Full scale on our cluster: tach **4026**, speedo **4138**, coolant 1428, fuel 1444.
* **One blocker remains: we do not know which RAM cells hold the needle angles.** Everything
  in Stage 2-3 exists to answer that. It cannot be answered offline — see §8.4.

## Safety rules (unchanged, non-negotiable)

1. **No `WriteEeprom` / `LoadEeprom` before Stage 7.** All experiments use `WriteRAM`, which
   is volatile: ignition off/on wipes it completely.
2. Car **parked, handbrake on**, engine off unless a step says otherwise, ventilated.
3. **One change per step**, logged. Know the undo before you write.
4. Cluster misbehaving or unresponsive → **ignition off, wait 10 s, on**. That is the reset.
5. Only one of {`ActuatorTest`} and {`ReadRAM`/`DumpMem`} can use the bus at a time.

## Setup

Every command below is run through `session.sh`, which fills in the port, the baud rate and
the controller address, and appends the command and its complete output to `logs/`. The
protocol's "log every command and its output" rule is otherwise easy to lose track of
across a session that is dozens of separate invocations.

```
cd ~/Documents/Claude
./session.sh port                      # plug the cable in first; auto-detects
./session.sh run ReadIdent             # == ./kw1281test <PORT> 10400 17 ReadIdent
./session.sh log                       # everything so far
```

**Which PORT form to use on macOS.** Two code paths exist in kw1281test and only one works
on this machine:

* `/dev/cu.usbserial-XXXX` → `GenericInterface` (.NET `System.IO.Ports`). **Use this.**
  Verified here on 2026-08-27 with a dry run against `/dev/cu.debug-console`: the port
  opened, the 5-baud wakeup and break toggling executed, and it failed only at the expected
  point (no controller answering). macOS granted device access with no prompt. This is what
  `session.sh port` picks.
* An 8-character FTDI serial number (`AABBCCDD`) → `FtdiInterface`, which `dlopen`s
  `libftd2xx.dylib`. **That library is not installed on this machine** — there is no
  `/usr/local/lib` and no Homebrew — so this form fails immediately. Installing FTDI's D2XX
  package is the fallback only if the `/dev/cu.*` path misbehaves with the real cable.

Gatekeeper quarantine is already cleared on this copy. A fresh copy that exits silently
with code 137 needs `xattr -d com.apple.quarantine kw1281test` once.

**One write per invocation.** `WriteRAM` takes exactly one ADDRESS/VALUE pair, and each
invocation re-runs the full KWP handshake (only the EDC15 command accepts multiple pairs).
So a 16-bit needle value costs two invocations plus two handshakes — fine for Stage 3, and
the reason Stage 4's sweep is paced in coarse steps rather than smooth ones.

---

# Stage 0 — bench work, no car needed

| # | Task | Why |
|---|---|---|
| 0.1 | Solve the EEPROM **address scrambling** (EEPROM_MAP.md §3.1) | We hold both `eeprom.bin` (scrambled) and `eeprom_decrypted.bin` (plain) for the same cluster, so the permutation is directly solvable. Needed only if we ever want to *write* parameters; **not** required for the sweep, since the patch block is written raw. Nice-to-have. |
| 0.2 | Re-read `reference/PatchModule.md` and `reference/ReadMe.md` | The install procedure and the failure modes. |

Stage 0 is optional. The sweep does not depend on it — `LoadEeprom` writes the patch region
raw, unencrypted (that is why the reference `.bin`s are plaintext 6502).

---

# Stage 1 — comms and identity (5 min, zero risk)

**Goal:** confirm we are talking to the cluster we analysed. If anything mismatches, **stop**
— every address in this project becomes invalid.

```
./kw1281test <PORT> 10400 17 ReadIdent
./kw1281test <PORT> 10400 17 ReadSoftwareVersion
```

Expect `6Q0920843`, `VDO V06`, coding `01144`, and `VQMJ07HH 08.40`.

Then verify the ROM in the car is the ROM in our files:

```
./kw1281test <PORT> 10400 17 ReadROM 0x2089     -> AD
./kw1281test <PORT> 10400 17 ReadROM 0x208A     -> B4
./kw1281test <PORT> 10400 17 ReadROM 0x208B     -> FF
```

**Extra check, new — verify our hook point actually contains what we think:**

```
./kw1281test <PORT> 10400 17 ReadROM 0x6584     -> 7C
./kw1281test <PORT> 10400 17 ReadROM 0x6585     -> 5F
./kw1281test <PORT> 10400 17 ReadROM 0x6586     -> 63
```

`7C 5F 63` = `JMP ($635F,X)`. This is the single most important read of Stage 1: it confirms
the hook we designed in §9.5 exists at that address in the live ROM.

> **GO/NO-GO:** all nine bytes match → proceed. Any mismatch → stop, report, re-derive.

---

# Stage 2 — find the needle angle cells (the crux)

**Goal:** the address of the four-gauge register block. Everything downstream depends on it.

**No steady revving is required.** An earlier draft of this plan asked for held rpm during a
`DumpMem`, which was not workable — see the timing table below. The approach here needs one
snapshot taken at **warm idle, parked**, which holds itself.

### 2.0 How long a dump actually takes

`DumpRom` (what was used for the ROM banks) transfers **8 bytes per bus round trip**;
`DumpMem` transfers **15**, so it is nearly twice as fast per byte. Calibrating against the
measured ROM dump (32 KB in ~25 min = 4096 round trips) gives ~0.37 s per round trip:

| command | bytes | round trips | time |
|---|---|---|---|
| `DumpMem 0x0200 0x0100` | 256 | 18 | **~7 s** |
| `DumpMem 0x0000 0x0400` | 1024 | 69 | ~25 s |
| `DumpMem 0x0000 0x1000` | 4096 | 273 | ~100 s |
| single `ReadRAM` | 1 | 1 | ~0.4 s |

Time the 256-byte dump and tell me the real number — it recalibrates everything above.

### 2.1 One capture, warm idle, parked

Both reference clusters keep their gauge block in `$02xx` (§9.4), so start narrow:

```
./kw1281test <PORT> 10400 17 DumpMem 0x0200 0x0100 ram_idle_02xx.bin
```

Engine **warmed up** and idling, car parked. Nothing to hold — just let it idle.
If that comes up empty, widen to `DumpMem 0x0000 0x0400`, then `0x0000 0x1000`.

### 2.2 Search for the block signature

```
python3 gaugecal.py block eeprom_decrypted.bin ram_idle_02xx.bin 0x0200
```

This does not hunt for one number. Three of the four gauges sit at values we can predict
with **zero driver effort**, and the four gauges are four consecutive 16-bit cells:

| gauge | expected | why it is free |
|---|---|---|
| coolant | **737** exactly | 74-116 °C all map to the same angle — the flat zone. Any warm engine reads 737 and stays there. |
| speedometer | **7** exactly | parked |
| tachometer | **~413** | idle holds itself, no pedal |
| fuel | 48..1444 | range check only |

Matching four adjacent cells at once is far more selective than matching one. On a synthetic
test the correct block came back as the only 4/4 hit, with no false positives.

If idle is not ~840 rpm, pass the real value: `... block ... 0x0200 950`.

### 2.3 Confirm with ReadRAM, not another dump

Once a block is proposed, confirm it in seconds rather than minutes. `ReadRAM` is one round
trip (~0.4 s), so a rough, brief blip of the throttle is enough — no steady hold:

```
./kw1281test <PORT> 10400 17 ReadRAM 0x<tach>      # low byte
./kw1281test <PORT> 10400 17 ReadRAM 0x<tach+1>    # high byte
```

Read at idle, then again while holding roughly 2000-2500 rpm for a couple of seconds. The
value must rise and come back. Precision is not needed — direction and rough magnitude are.
For reference, ~1005 at 2000 rpm and ~1260 at 2500.

> **Fallback if 2.2 finds nothing:** the engine may not have been warm (the coolant anchor is
> the strongest of the three — check it first). Then widen the dump range. Then
> `python3 ramdiff.py` between an engine-off and an idle capture — both effortless states —
> and look for 16-bit cells that moved. Only as a last resort use `find` with held rpm.

> **GO/NO-GO:** a 4/4 block confirmed by ReadRAM → Stage 3. Nothing → do not improvise at the
> car; keep the dumps, go home, analyse offline.

---

# Stage 3 — prove we can move a needle (engine OFF)

**Goal:** write an angle and watch the needle obey. Fully reversible, volatile.

Engine **off**, KL15 **on** — the control loops are quiescent and the needle rests at zero,
so any movement is unambiguous.

Write the tach to roughly half scale (2009 = `$07D9`, the ~3969 rpm point):

```
./kw1281test <PORT> 10400 17 WriteRAM 0x<A>   0xD9      # low byte
./kw1281test <PORT> 10400 17 WriteRAM 0x<A+1> 0x07      # high byte
```

Then full scale (4026 = `$0FBA`): low `0xBA`, high `0x0F`. Then back to rest (14 = `$000E`):
low `0x0E`, high `0x00`.

**Report after each write what the needle did:** jumped and held / jumped and fell back /
twitched / nothing.

* **Jumped and held** → we own the needle. Map the other three gauges the same way, then
  Stage 4.
* **Jumped then fell back** → the normal loop is overwriting us. We need the gate. Candidates
  to try, in order: the flags byte at the end of the gauge block; ZP `$86` (the dispatcher's
  state byte, §9.5); the reference's analogues of `$0B2C` bit5 and ZP `$81` bit1. Read them
  first (`ReadRAM`), change one bit, retry the write.
* **Nothing** → wrong cell, back to Stage 2.

### 3.1 Also worth 30 seconds (and only 30)

```
./kw1281test <PORT> 10400 17 WriteRAM 0x026E 0xCF
./kw1281test <PORT> 10400 17 WriteRAM 0x026F 0x0F
```

This is the retracted "shortcut" from the reference ROM. On our analysis `$026C-$0273` is a
timer (§8.1) and **nothing should move**. If a needle *does* move, the static analysis is
wrong — say so immediately, it changes everything.

> **GO/NO-GO:** a needle obeys a written angle and holds → Stage 4.

---

# Stage 4 — host-driven sweep (the milestone, still no EEPROM)

**Goal:** reproduce the full sweep from the laptop. This proves the motion, captures the
timing, and gives us the exact profile the patch must reproduce. Nothing is persisted.

**A constraint discovered on 2026-08-27, before touching the car.** The original text here
called for "~30-40 steps, ~20-40 ms apart". That is not achievable with the tool as it
ships: `WriteRAM` accepts one ADDRESS/VALUE pair per process, and every process re-runs the
KWP handshake. Real cost is therefore seconds per byte, not milliseconds. Two ways forward:

* **4a — coarse, works tonight, zero setup.** Step the needles through ~6-8 positions with a
  pause between each: `14 → 1000 → 2000 → 3000 → 4026 → 3000 → 2000 → 14`. This is a
  staircase, not a sweep. It still proves everything Stage 4 must prove — that the cells
  drive the needles across the *whole* range, that full scale is reachable, that the
  hardware's own slew rate is (per §9.2) what actually smooths the motion. It just does not
  look like the finished product.
* **4b — real sweep, needs a build step.** Extend `WriteRAM` in `Program.cs` to accept
  multiple ADDRESS/VALUE pairs in one session, the way `WriteEdc15Eeprom` already does
  (`addressValuePairs` is already parsed there). One handshake, then N writes back to back.
  Cost: the .NET 10 SDK is **not installed** on this machine, so this means a ~200 MB
  download plus a `dotnet publish`. The source change itself is a few lines.

4a is the honest first move: it gates Stage 5 on its own, and if it reveals the needles
behave differently than expected, 4b would have been wasted effort. Decide after Stage 3.

Watch for:
* Does the needle track smoothly, or does the cluster apply its own damping? (The reference
  patch just slams the value and lets the hardware slew — see §9.2, it writes full deflection
  and holds it for 11 counter ticks.)
* How long does a full sweep take, and does it look right?

> **GO/NO-GO:** a clean visual sweep → Stage 5. This is the point at which the project is
> proven; everything after is packaging.

---

# Stage 5 — collect the remaining patch ingredients (car, read-only)

Four unknowns remain (§9.6). All are `ReadRAM`/`DumpMem` work, no writes.

| # | Unknown | How to find it |
|---|---|---|
| 5.1 | **Timeline counter** (their `$0CB8`) | Need a cell that counts slowly enough that 0→27 spans ~2-3 s. Take `DumpMem 0x0000 0x1000` twice, ~2 s apart, at rest; diff; look for a byte that advanced by a small amount. Verify with a third capture. |
| 5.2 | **State gate**: value of ZP `$86` | `ReadRAM 0x86` at rest, at KL15-on, and while running. The dispatcher does `LDA $86; ASL; TAX; JMP ($635F,X)`, so `$86` picks the table entry — we need to know which value is live during the first seconds after power-up. |
| 5.3 | **Which X to act on** | Follows from 5.2: X = `$86` * 2. |
| 5.4 | **Real gauge value sources** (their `$16B0`/`$1720`) | Possibly skippable: if we sweep only in the first ~2 s after power-up with the engine off, the real values are zero and the normal loop will take over on its own once the patch stops acting. Prefer skipping — fewer addresses to get wrong. |

> **GO/NO-GO:** 5.1 and 5.2 answered → Stage 6.

---

# Stage 6 — build the patch (bench, no car)

1. Write the sweep in 6502, based at **$0EBE**, modelled on §9.2 but using **our** gauge
   block, **our** counter, **our** gate, and ending with **`JMP ($635F,X)`** (`7C 5F 63`).
   Keep the patch's own variables in the last bytes of the code block, as the references do.
2. Buffer limit: `$0EBE`..`$14F4` ≈ 1590 bytes. The references fit in 158. No pressure.
3. Assemble; build the block:
   * data block: `00 65 84 4C / 00 65 85 BE / 00 65 86 0E / 00 0F`
   * header: H2/H3 = code length LE, H1 = 14 (data length; set bit7 per §2 as desired),
     **H4 = `$40`**, **H5 = `$08`** (or `$BD` in the checksum form), H0 = checksum per §2
   * constraints: data len > 3, data len mod 4 == 2, (code+data) even, H0 ≠ H1
4. **Verify with our own parser before writing anything** — the same script that validated
   all five reference patches. If it does not reproduce our H0, do not proceed.
5. Sanity-check H4/H5 against `ReadSoftwareVersion` one more time.

---

# Stage 7 — install (separate, deliberate session)

Do **not** mix this with discovery work. Fresh session, unhurried, backup in hand.

```
# 1. BACK UP. Keep this file safe. Do not skip. Do not overwrite it.
./kw1281test <PORT> 10400 17 DumpEeprom 0 2048 ClusterBackup_$(date +%Y%m%d).bin

# 2. Inspect $552 first — it must look empty/idle (H0 == H1, or 0xFF/0x00).
#    If something is already there, stop and work out what it is.

# 3. Install
./kw1281test <PORT> 10400 17 LoadEeprom 0x552 our_sweep.bin

# 4. Reset, then ignition off, wait 30 s, on.
./kw1281test <PORT> 10400 17 Reset
```

**If it does not boot:** restore `ClusterBackup_*.bin`. Worst case this requires opening the
cluster and using an EEPROM programmer on the 93C86 under the white light mask
(EEPROM_MAP.md §1). That is the real risk of this stage, and it is why Stages 3-4 must fully
prove the motion first.

**Never install a block whose H4/H5 do not match.** The loader should refuse it, but do not
use the loader as the safety net.

---

## Risk summary

| Stage | Risk | Recovery |
|---|---|---|
| 1, 2, 5 | none (read-only) | — |
| 3, 4 | needle moves, cluster may glitch | ignition off/on |
| 6 | none (bench) | — |
| 7 | **cluster may not boot** | restore backup; worst case open the cluster + programmer |

## One-line status

Mechanism understood, hook found, needle units known, install procedure verified —
**blocked only on locating four RAM cells, which needs the car.**

---

# COMPLETED 2026-08-27

Every stage of this plan has been carried out and the sweep is installed in EEPROM. The
plan is kept as a record of the route; do not re-run it.

Where it turned out to be wrong, and what actually happened:

* **Stage 2** found `$0264-$026B` correctly, but those cells are a *mirror* — a faithful
  report of needle position that the driver does not read. The real control is four
  11-byte structs in zero page at `$00D4`/`$00DF`/`$00EA`/`$00F5`
  (`PATCH_ENGINEERING.md` §14-§15).
* **Stage 3** could never have worked as written, because writing the mirror does nothing.
  Both the mirror and the demand are rewritten continuously by the cluster; only the two
  filter stages inside the struct are ours to drive (§16.1).
* **Stage 4**, the host-driven sweep, was skipped. The bus is far too slow to drive a
  motion profile, and once the patch machinery was proven in RAM it was the better vehicle.
* **Stage 5** — full-scale calibration was never needed: motor steps are the 1/16-degree
  needle angle plus 21, straight out of `$5BE3`, so every full-scale value was already in
  our own EEPROM dataset (§15.2).
* **Stage 7** install had to be done four times, because the question "when is it safe to
  start?" took four attempts to answer. The three wrong guards and why each failed are in
  §16.3 — the working one waits a fixed 255 dispatches after the gate opens.

Current state, undo commands and day-to-day use: `FIELD_CHEATSHEET.md`.

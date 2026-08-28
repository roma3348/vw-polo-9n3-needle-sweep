# Needle-sweep patch — engineering dossier (VQMJ07HH-08.40, 6Q0920843 VDO)

This documents the built-in patch mechanism and everything extracted from OUR ROM needed
to build a needle-sweep patch, cross-referenced against gmenounos/vwcluster reference
patches. Source of the mechanism: `PatchModule.md`, `NeedleSweep/*`, `VDO EEPROM
Encryption.md` in that repo.

## 1. The mechanism (how the reference sweeps work)
The cluster SoC (CDC16xxF-E) has a hardware **Memory Patch Module**: it can replace a few
ROM bytes in real time. At boot the ROM reads a **patch block from EEPROM**, validates it,
copies the code+data into RAM, and programs the patch module so a chosen ROM location is
overwritten — typically with a `JMP` into the copied code in RAM. The needle-sweep is such
a patch: a small 6502 routine that drives the gauge working-registers min→max→min once at
power-up, then hands control back to the normal code path.

Reference install flow (per ROM version, plaintext patch loaded raw):
```
kw1281test COMx 10400 17 DumpEeprom 0 2048 backup.bin      # ALWAYS back up first
kw1281test COMx 10400 17 LoadEeprom <ADDR> NeedleSweep-<ROM>.bin
kw1281test COMx 10400 17 Reset
# ignition off, wait 30s, on -> needles sweep
```
kw1281test writes EEPROM **raw** (no encryption in the tool); reference patch `.bin`s are
plaintext 6502. So the patch region is stored linearly/plaintext, NOT in the scrambled
parameter store.

## 2. Patch-block format (validated against our loader)
6-byte header H0..H5, then code block, then data block.
- H0 = checksum: sum mod 256 of all bytes in the block except H0 and bit7 of H1; if that
  sum equals H1&0x7F, use its complement. (Our loader computes exactly this at $F0D2-$F0F5.)
- H1 = data-block length in low 7 bits; bit7=1 → install always, bit7=0 → install after
  reset only.
- H2/H3 = code-block length (LSB/MSB).
- H4 = must equal ROM_VERSION[8]. **For us = $40.**
- H5 = if bit7=0, must equal ROM_VERSION[9] (**$08**); if bit7=1, must equal
  ((sum of version bytes except [8]) OR 0x80) = **$BD**.
- Data block = N×4 bytes `{addrHi, addrMid, addrLo, replByte}` (PHYSICAL ROM address) then
  2 trailer bytes = PER1,PER0 = high/low of `2^(N+1) − 1`.
- Constraints: data len > 3; (data len) mod 4 == 2; (code+data) even; H0≠H1.

## 3. OUR ROM — extracted facts (VQMJ07HH-08.40)
- ROM version block at CPU **$21B3**: `56 51 4D 4A 30 37 48 48 40 08` = "VQMJ07HH" $40 $08.
  → ROM_VERSION[8]=**$40**, [9]=**$08**. H4=$40; H5=$08 (or $BD in checksum form).
- Patch loader: bank2 **$EFAE** (callers bank0 $6496, $6707). Validates header in zero-page
  $14..$19 = H0..H5, checks H0≠H1, H1 bit7, H4 vs $21BB, H5 vs $21BC/checksum, size vs
  RAM buffer, then copies and programs the patch module. Logic matches PatchModule.md 1:1.
- **RAM patch code buffer: start $0EBE, end $14F4** (from ROM constants $5139/$513A and
  $513B/$513C). ~1590 bytes available. (Reference VWK501 used $0E7A/$0E7C — ours is $0EBE.)
- **Memory Patch Module I/O registers** (from $F116-$F14E):
  `$1E64`=addr low, `$1E65`=addr mid, `$1E66`=addr high, `$1E67`=replacement data,
  `$1E68`=PER0, `$1E69`=PER1.
- **Patch EEPROM address:** loader reads the header via pointer $0E/$0F = **$02A9** = 10-bit
  word $2A9 = **byte $552**. This equals the Polo `VSQX01LM` reference address
  (`LoadEeprom 0x552`) — strong confirmation. **To verify at bench** (read-only) before any
  write: `DumpEeprom 0 2048 dump.bin` and inspect bytes at $552 (and $4F4/$4F6) — an
  un-patched cluster should show an idle/empty header (H0==H1 or 0xFF/0x00).

## 4. The "control" surface (what the sweep code drives)
> ⚠️ **The conclusion of this section (last paragraph) is RETRACTED — see §8.1.** The
> description of the *reference* ROM below is still accurate; the inference that our ROM
> uses the same cells is not. On VQMJ07HH-08.40, $026C-$0273 is a timer.

Disassembly of the reference VWK501MH-00.88 sweep code (runs at its RAM base $0E7A) shows it
writes the **gauge working-registers $026C–$0273** — the SAME addresses the user already
probed on THIS cluster and saw change with ignition/idle/revs:
```
sweep-up phase:  STA $026E/$026F/$0270/$0271 = large value (e.g. $0FF1CF) -> full deflection
sweep-down/idle: STZ $026E..$0271                                        -> needle to 0
restores real gauge values from $16B0/$16B1, $1720/$1721; sequences via counter $0CB8;
gates on mode byte $8A (==$0E) and its own flags $0F16/$0F17; ends by JMP ($565F,X)
back into the original hooked code path.
```
~~Implication: on our cluster the gauge control registers are almost certainly the same
`$026C–$0273` … the single biggest de-risk: we already know what to write.~~
**RETRACTED (§8.1).** Our ROM's $026C-$0273 is a 17-bit elapsed-time counter + deadline +
flags. The de-risk does not exist; our gauge registers are still unknown.

## 5. What still must be resolved to BUILD our patch
1. ~~**Confirm gauge control live**: `WriteRAM 0x026E 0xFF` / `0x026F 0x0F` / `0x0270 0xF1` /
   `0x0271 0x0F` and watch the tachometer.~~ **SUPERSEDED — see §8.1/§8.5.** On our ROM those
   are timer cells; keep the write as a 30-second sanity check only, and expect no needle
   movement. Finding the real gauge registers is now empirical work (Phase 1 rev-correlation).
2. **Find our hook point**: the reference intercepts a point in the gauge/main loop
   (VWK501 physical $5749) and restores via `JMP ($565F,X)`. We must find the equivalent in
   OUR ROM — the routine that services the gauges each cycle — and the original bytes to
   preserve/redirect. Candidate: the gauge/timer service near our $026C-$0273 writers
   (start from `vdo_toolkit.py xref 0x026E` and the timer at bank2 $F868).
3. **Map CPU↔physical addresses** for the data block. The data block uses the SoC PHYSICAL
   flash address of the hook (not the CPU address). Mapping table (PatchModule.md):
   `$00000-$07FFF→$00000-$07FFF`, `$08000-$0FFFF→$18000-$1FFFF`,
   `$10000-$17FFF→$28000-$2FFFF`, `$18000-$18FFF→$38000-$3FFFF`, `$20000-$28FFF→$48000-$4FFFF`.
   Our bank layout (CPU $2000-$7FFF fixed = bank0; $8000-$FFFF = paged banks) must be tied to
   physical flash offsets — determine which physical page each CPU bank corresponds to
   (needs the CDC16xxF-E manual or a couple of ReadROM probes comparing known bytes).
4. **Author the sweep code** for RAM base **$0EBE**, using ~~$026C-$0273~~ **the gauge
   registers established empirically in Phase 2 (§8.1: NOT $026C-$0273)** and
   our hook's restore vector; assemble; build the block; compute H0; set H1 bit7 as desired;
   H4=$40,H5=$08/$BD; trailer for N patches.
5. **Install**: `DumpEeprom` backup → `LoadEeprom 0x552 our_patch.bin` → `Reset` → power
   cycle. If it fails to boot, restore `backup.bin` (may require opening the cluster + an
   EEPROM programmer in the worst case — see risk note).

## 6. Porting shortcut (recommended)
This is exactly how the BitFab patches were made: take a reference patch (the Polo-family
`VSQX01LM-01.00`, base $552 like ours) and re-point its addresses to ours. The gauge regs
likely match ($026C-$0273) — **no longer believed, see §8.1**; the main edits are RAM base
($0E7A→$0EBE), the gauge registers (must be found, §8.4), the hook point +
restore vector, its own flag addresses, and the state vars $16B0/$1720/$0CB8/$8A/$0B2C.

## 7. Risk note (from the reference readme, applies to us)
A wrong patch may simply not run — or the cluster may fail to boot, recoverable only by
opening it and restoring the EEPROM with a programmer. Therefore: always DumpEeprom backup
first; only LoadEeprom a block whose H4/H5 match our version; prototype all motion via
WriteRAM (volatile) before ever writing EEPROM; keep the backup safe.

## 8. Offline static analysis (2026-08-26/27, no car) — CORRECTION to §4/§6 assumption
Static xref/disasm of the .bin files only; nothing here is confirmed on live hardware.
Tooling: `pip3 install py65`, then `vdo_toolkit.py xref`/`dis`.

### 8.1 `$026C-$0273` on OUR ROM is a TIMER, not the gauge registers
This contradicts the §4/§6 working assumption ("gauge control registers are almost certainly
the same $026C-$0273"). That assumption came from the reference ROM VWK501MH-00.88; on
**our** VQMJ07HH-08.40 these cells decode unambiguously as an elapsed-time counter:

- `$026E:$026F` = free-running 16-bit counter. `$F81A` increments it by 1 on every *other*
  dispatcher tick (half-rate divider = `$0273` bit5, toggled at `$F84C-$F851`).
- `$0270:$0271` = deadline, computed by `$F893` as **`counter − 0x3C`** — a needle *target*
  computed as "current position minus 60" is meaningless; a wrap-around deadline is not.
- `$0273` flags: bit4 = "deadline computation underflowed/wrapped" (set at `$F8AF`, cleared
  at `$F8A8`, also set at `$F862-$F864` when the counter itself wraps `$FFFF`→`$0000`);
  bit5 = half-rate divider parity; bit6 = counter bit-16 (carry-out); bit7 = state flag.
- `$0272` = 0..0x14 (20) tick counter (`$F868-$F88F`); on reaching 20 it re-arms via `$F893`
  and clears `$0273` bit7.
- **The decisive evidence** — bank0 `$634A` is the *reader* of this quantity:
  `CLC / LDA $0273 / BIT #$40 / BEQ +1 / SEC / LDA $026F / ROR A / PHA / LDA $026E / ROR A /
  TAX / PLA / RTS` — it returns the **17-bit** counter (bit16 taken from `$0273` bit6) shifted
  right by one, in A:X. A gauge needle position needs no 17-bit range fed by a carry flag;
  an elapsed-time counter does.

Consequence: **the "biggest de-risk" claimed in §4 does not hold for our ROM.** The reference
patch's addresses must be re-derived, not reused. Note also that the user's earlier
observation that these cells "change with ignition/idle/revs" does **not** discriminate — a
free-running counter changes continuously regardless of engine state. (CLAUDE.md §"Reality
check" already hinted at this by calling it "our timer analysis at $026E-$0273"; §4 of this
dossier then over-committed to the gauge reading.)

Gates on that timer, for reference: every entry in the block starts `LDA $095A / BIT #$01 /
BNE <skip>` — `$095A` bit0 suspends the update for that tick. `$095A` is a heavily-shared
global mode byte (100+ read sites across all banks), not a dedicated flag. Secondary gate:
`$0991` bit1 at `$F82D`.

### 8.2 Ruled out as the gauge driver (static)
- **bank1 `$BE9D`/`$BEA4`/`$BEB0`** — these are actuator-test *predicates*, not gauge acts:
  they read status bits `$15A2`/`$15A6`/`$15A7`/`$15AC`/`$15AD` and step `$0713`/`$0714`,
  which are sequence-position counters for the test. (CLAUDE.md describes them as
  "per-gauge routines / gauge-select" — that reading is not supported by the disassembly.)
- **`$1F2D`/`$1F2E`/`$1F2F`** — the buzzer/gong tone generator. `$1F2D` = level, clamped
  against max `$1590`; 4-byte tone table at bank3 `$ECE8`; duration counters `$08B9`/`$08BB`
  decrement to silence via `STZ $1F2D`. Drivers at bank0 `$4900`, bank3 `$EDD0-$EE49`.
- **`$1F80-$1F92`** — peripheral configuration, not per-tick data. bank2 `$E960`, `$E9A4`,
  `$E9F4` are three init variants writing constants, each mirrored into a zero-page shadow
  at `$0045-$0050`.

### 8.3 Main per-cycle service loop — bank0 ~$6480-$6B60+ (partially mapped)
A single flat routine JSR-ing through ~150 subroutines in sequence: the cluster's main cyclic
task (all subsystems, not just gauges). It is the realistic hook region per §5.2. Calls into
the timer block above: `JSR $F7EB` @ `$64E0`, `JSR $F81A` @ `$6912` and `$69AA`,
`JSR $F7FB` @ `$6A1B`, `JSR $F806` @ `$6B3C`. Not yet traced: what calls this dispatcher
once per tick. (`$F868` has no absolute-addressed caller — reached indirectly or vestigial.)

### 8.4 Open — the actual gauge output registers were NOT found offline
No routine writing a computed needle position to hardware has been identified. The I/O space
`$1F00-$1FFF` contains several 8-byte-spaced register blocks (`$1F88`, `$1F90`, `$1F98`,
`$1FC0`, `$1FD0`, `$1FD8`…) that are plausible PWM/stepper channels — 4 gauges x 2 coils
would need 8 — but this is pattern-matching, not evidence; the only routines found writing
them write configuration constants (§8.2). **This must be resolved empirically.**

### 8.5 Revised plan consequence for the live session
Phase 2's "big shortcut" (WriteRAM to `$026E-$0271`) should be demoted from primary to a
30-second sanity check: it is safe (volatile RAM, timer only, ignition-cycle recoverable) and
cheap, but on this analysis it will perturb a timeout rather than move a needle. **Phase 1
rev-correlation is now the primary path to finding the gauge cells**, and Phase 2 becomes
"write to the Phase-1 candidates", i.e. the CLAUDE.md "fallback" branch is the real branch.
Budget the session accordingly.

## 9. Reference patches analysed (2026-08-27) — the design is now settled
The five upstream sweep patches are in `reference/`. Our parser reproduces all five H0
checksums exactly, so §2's format description is confirmed byte-for-byte.

### 9.1 The universal pattern: a JMP trampoline
Every reference patch replaces **exactly 3 ROM bytes** — an existing `JMP (TABLE,X)`
instruction — with `4C lo hi` = `JMP <patch base in RAM>`, and then **ends by executing the
very instruction it replaced**, `JMP (TABLE,X)`. A textbook trampoline.

| ROM | hook (physical) | replacement | entry / RAM base | restore vector |
|---|---|---|---|---|
| VSQX01LM-01.00 | `$0042F1` | `4C D5 09` | `$09D5` | `JMP ($4207,X)` |
| VWK501MH-00.88 | `$005749` | `4C 7A 0E` | `$0E7A` | `JMP ($565F,X)` |
| VWK502MH-09.00 | `$0055DA` | `4C 7B 0E` | `$0E7B` | `JMP ($54F0,X)` |
| KB5M07HH-09.00 | `$005626` | `4C 90 0E` | `$0E90` | (+1 extra patch `$0136D5<-$1F`) |

**§5.3 is resolved.** Every hook address is `$0042F1..$005749`, which falls in the identity
region of the mapping table (`$00000-$07FFF -> $00000-$07FFF`). So for a hook in bank0
(`$2000-$7FFF`) the **physical address equals the CPU address** — no bank arithmetic needed.
We only need the mapping if we ever hook a paged bank, which we will not.

### 9.2 The sweep algorithm (VWK501MH-00.88, disassembled at $0E7A)
```
PHY ; Y = $0CB8                  timeline counter (cluster tick, not the patch's own)
gate: ZP $8A must equal $0E      cluster state gate
gate: X (dispatch index) in {6, 8, $1E}
  Y <  3        -> STZ gauges                (rest)
  3 <= Y < $0E  -> gauges = $0FCF / $0FF1    (full deflection)
  $0E <= Y< $1B -> STZ gauges                (return)
  Y >= $1B      -> done, fall through forever
then: restore untouched gauges from $16B0/$1720, clear ZP $81 bit1 and $0B2C bit5,
      rewrite the return address on the stack, and JMP ($565F,X)
```
Its own state lives in the **last 6 bytes of the code block** (`$0F12/$0F16/$0F17` for a
`$0E7A` base) — self-contained, no cluster RAM needed for the patch's own flags.

### 9.3 The decisive confirmation: gauge registers hold 1/16-degree angles
The reference sweep writes **`$0FCF` = 4047** and **`$0FF1` = 4081** for full deflection.
Our EEPROM scale curves independently give full-scale **4026** (tach) and **4138** (speedo)
— see EEPROM_MAP.md §4. Two completely independent sources landing in the same numeric band
(~250-256 degrees at 1/16 deg per LSB) settles it: **the gauge register is a 16-bit needle
angle in sixteenths of a degree.** For our cluster the sweep must ramp to 4026 / 4138.

### 9.4 The gauge register block is ROM-specific — which is why ours is not $026C
Diffing the VWK501 and VSQX01LM code blocks (158 bytes, only 44 differing, all of them
address operands) shows the gauge block simply moves: **VWK501 = `$026C-$0273`,
VSQX01LM = `$0271-$0278`** — the same structure shifted by 5. Layout in both:
four 16-bit gauges at `+0, +2, +4, +6` plus a flags byte at `+7`; the sweep drives the two
middle ones (tach, speedo) and restores the outer two from the cluster's real values.
This independently supports §8.1: there is no reason our block would be at `$026C`, and we
have positive evidence it is a timer there instead. **Both reference blocks live in `$02xx`,
so ours very likely does too.**

### 9.5 Our hook point — found
Bank0 contains the exact structural twin of the reference hook, inside the **main loop**:
```
$6575  JSR $684A          ; \
$6578  JSR $67F7          ;  |  main loop
$657B  JSR $6580          ;  |
$657E  BRA $6575          ; /
       ...
$6580  LDA $86            ; cluster state byte  (their $8A analogue)
$6582  ASL A
$6583  TAX
$6584  7C 5F 63  JMP ($635F,X)     <-- HOOK HERE
```
The table at `$635F` holds coherent bank0 targets (`$66F4, $6587, $67A5, $638E, $6591,
$668A, $6771`, then `$638E` repeated as the default), confirming it is a real dispatch table.
(A second apparent `JMP ($659B,X)` at `$6643` is a linear-disassembly artifact — `$659B` is
code, not a table. Ignore it.)

**Therefore our data block is:**
```
00 65 84 4C      ; $6584 <- $4C
00 65 85 BE      ; $6585 <- $BE
00 65 86 0E      ; $6586 <- $0E   => JMP $0EBE
00 0F            ; trailer, 3 patches: 2^(3+1)-1 = 15
```
and our patch code, based at **$0EBE**, must end with **`JMP ($635F,X)`** (`7C 5F 63`).

### 9.6 What is still missing before code can be written
1. **Our gauge register block** — the one true blocker. Find it live (see TEST_PLAN.md).
2. **Our timeline counter** (their `$0CB8`): a cell that ticks slowly enough that 0->27
   spans a couple of seconds. Must be identified live, or replaced by our own counter in the
   patch tail driven off a known tick.
3. **Our state gate value** for ZP `$86`, and which dispatch indices X to act on.
4. **Real-gauge-value sources** (their `$16B0`/`$1720`) — possibly avoidable if we sweep only
   at power-up with the engine off, when the real values are zero anyway.

---

# 10. Live session at the car, 2026-08-27 — what the hardware actually says

## 10.1 The gauge register block: `$0264-$026B` (CONFIRMED)

| address | gauge | ignition on, engine off | engine idling |
|---|---|---|---|
| `$0264` | fuel | 514 | 514 |
| `$0266` | coolant | 67 | 67 |
| `$0268` | tachometer | 14 | **406** |
| `$026A` | speedometer | 7 | 7 |

Four consecutive 16-bit little-endian cells holding needle angles in 1/16 degree, exactly
the layout §9 describes for the reference patches. Located from a single
`DumpMem 0x0200 0x0100` snapshot (19 s) by matching all four predicted rest angles at once;
sole 4/4 hit in the range, no runner-up. Confirmed by starting the engine: only the tach
cell moved, 14 -> 406, which the tach curve inverts to **824 rpm** — an ordinary idle. One
variable changed in the car, one cell responded.

It sits immediately *before* the `$026C` timer, which is exactly why the reference ROM's
`$026C` looked plausible in §8: the same structure, shifted eight bytes.

## 10.2 ~~These cells cannot be driven from the laptop~~ — RETRACTED same day

The original claim here was that `WriteRAM $0268 0xD9` was accepted, the needle did not
move, and a read-back showed 14, therefore the cluster's loop wins the race against a
KW1281 round trip and the gauges cannot be driven from outside.

**That test never wrote what it said it wrote.** kw1281test has a bug: `WriteRAM` was
grouped in `Program.cs` with the read-by-address commands, which parse only `args[4]`. The
VALUE argument was never read, `value` kept its initialiser, and **every WriteRAM silently
wrote `0x00`** whatever was on the command line. So the cell went from 14 (rest) to 0 —
a difference of under a degree of needle travel, invisible by eye, and then the loop
restored 14. The observation is fully explained by the bug and says nothing about whether
the cluster's loop can be outrun.

Fixed in our build (§13). **Whether these cells can be driven from the laptop is once again
an open question**, and worth one honest retest: `WriteRAM 0x0269 0x07` writes the HIGH byte
alone, taking the tach cell from 14 to 1806 — a third of the dial — in a single command, so
even a brief deflection should be visible.

Note this does not change anything about §10.1: the block was identified by *reading*, and
confirmed by starting the engine and watching only the tach cell move. That evidence stands.

## 10.3 THE BIG ONE: this cluster already has a patch installed

`DumpEeprom` of the live cluster shows the patch region is **not empty**:

```
$550: 04 14 B9 8E 0E 00 40 BD C9 04 F0 04 C9 05 D0 03
$560: 4C 7B EC 4C 7D EC 01 6C 77 4C 01 6C 78 BE 01 6C
$570: 79 0E 00 0F FF FF FF ...
```

Parsed against the §9 format:

* header at `$552`: `B9 8E 0E 00 40 BD` — **H4=$40, H5=$BD**, matching the version bytes
  `ReadSoftwareVersion` reports for this ROM (`VQMJ07HH @ $08`, i.e. $40/$08-or-$BD).
* patch code: `CMP #$04 / BEQ +4 / CMP #$05 / BNE +3 / JMP $EC7B / JMP $EC7D`
* data block, three entries: `$016C77 <- $4C`, `$016C78 <- $BE`, `$016C79 <- $0E`
  — i.e. it writes `4C BE 0E` = **`JMP $0EBE`** over the instruction at physical `$016C77`.
* trailer `00 0F` = 3 patches (2^(3+1)-1 = 15). Block ends at `$572`; `$573` onward is `$FF`.

So a working patch of exactly the kind we intend to build is already resident, was written
by someone who knew this format, and is presumably a factory or service errata fix that
extends a comparison chain with two extra cases ($04 and $05).

**Three consequences, all of which change the plan:**

1. `$0EBE` is **taken**. Our code must live somewhere else in the patch RAM buffer
   (buffer runs to `$14F4`), placed after whatever the existing patch occupies.
2. The patch region at `$552` is **occupied**. We must extend this block — appending our
   three data entries, updating the trailer count and the H0 checksum — rather than
   overwrite it. Overwriting would remove a fix the cluster currently relies on.
3. The hardware patch module has a finite number of byte-substitution slots (6-10). Three
   are already spent. Our hook needs three more. Confirm headroom before designing.

## 10.4 Address mapping SOLVED (was an open question in §5.3)

The existing patch targets physical `$016C77` and returns to CPU `$EC7B`/`$EC7D`. That fixes
the mapping for the paged banks:

```
physical = bank_index * $8000 + (CPU_address - $8000)     for banks 1..4
physical = CPU_address                                     for bank0 ($2000-$7FFF)
```

Check: bank2, CPU `$EC77` -> `2*$8000 + $6C77` = `$016C77`. ✓
So our planned bank0 hook at CPU `$6584` encodes as physical `$006584`, as §9.5 assumed.

## 10.5 Our ROM dump is a PATCHED view, not raw ROM

`VQMJ07HH_bank2.bin` at CPU `$EC77` already contains `4C BE 0E` — the patched bytes, not
the original instruction. The Memory Patch Module substitutes bytes transparently on read,
so the dump captured the running state with the patch live. The original ROM bytes at
`$EC77-$EC79` are **not recoverable from our files**. Anywhere else we rely on the dump we
should remember it reflects three substituted bytes.

## 10.6 Which EEPROM file is which — settled

`DumpEeprom` returns the **descrambled dataset**, not the raw chip contents:

* new backup vs `eeprom_decrypted.bin`: 1988 of 2048 bytes identical, and byte-identical
  through the patch region — so `eeprom_decrypted.bin` really is this cluster, and every
  gauge curve we derived from it applies. The 60 differing bytes are drift since that dump
  (21 small regions; the 23-byte run at `$133-$149` is the best odometer candidate).
* `eeprom.bin` vs the backup: 5 bytes match, i.e. nothing. That file is the raw scrambled
  chip read, which is why `vdo_eeprom_codec.py` could not decode it — it never implemented
  the address-scrambling stage. **We never needed the codec.** Read parameters from a
  `DumpEeprom` output directly.

Correction to EEPROM_MAP.md: `$0E0` is **not** the odometer. It reads the VIN as ASCII text. The odometer formula recorded there needs re-deriving against
this backup.

## 10.7 Backup

`ClusterBackup_20260827.bin`, 2048 bytes, taken before any write. Keep it.

---

# 11. The patch loader, fully disassembled (bank2 `$EFAE-$F159`)

Read out of our own ROM on 2026-08-27 and then **verified by reproducing the installed
block byte for byte** (`patchblock.py verify` → ROUND TRIP MATCHES). This supersedes the
guesses in §4 and closes item 4 of §5.

## 11.1 Block format

Block lives at EEPROM byte `$552`; the loader reaches it as word index `$2A9`
(`$2A9 × 2 = $552`), reading 3 words = the 6-byte header into ZP `$14-$19`.

```
H0  checksum
H1  bit7 = "patch present";  bits0-6 = DATA length
H2  CODE length
H3  high byte of (code_len + data_len)
H4  must equal ROM $21BB
H5  bit7 clear -> must equal ROM $21BC
    bit7 set   -> must equal 0x80 | (ROM $21BC + sum of ROM $21B3..$21BA)
<code>  H2 bytes, copied to RAM $0EBE and executed there
<data>  H1&0x7F bytes: N entries of 4, then a 2-byte trailer
        entry   = addr_hi, addr_mid, addr_lo, replacement   (address BIG-endian, PHYSICAL)
        trailer = PER1, PER0
```

Ours reads `H4=$40, H5=$BD`; `$BD` has bit7 set, so it is the checksummed variant — which
is why §4 saw both `$08` and `$BD` in the wild.

## 11.2 Validation the loader performs (each one can reject our block)

| check | code | rule |
|---|---|---|
| "no patch" sentinel | `$EFCC` | `H0 == H1` ⇒ module disabled, block ignored |
| version gate | `$EFE8-$F00A` | H4 vs `$21BB`; H5 vs `$21BC` (+checksum if bit7) |
| non-zero length | `$F01B` | `code_len + data_len != 0` |
| buffer fit | `$F030` | total ≤ `$14F4 - $0EBE` = **1590 bytes** |
| even total | `$F03F-$F043` | total must be even |
| data shape | `$F106-$F10E` | data_len bit0 clear, bit1 set, result non-zero ⇒ `N*4+2` |
| final enable | `$F151` | PER0 bit0 must be set, else error flag into `$0944` bit7 |

## 11.3 Checksum (verified)

```
s  = (sum(code + data) + H5 + H4 + H3 + H2 + (H1 & 0x7F)) & 0xFF
H0 = (s ^ 0xFF)  if (s & 0x7F) == (H1 & 0x7F)  else  s
```
The conditional exists only so H0 can never collide with H1 and read as "no patch".

## 11.4 Programming the module (`$F114-$F152`)

```
mask = 0x0002
for each entry:
    $1E69 = mask>>8 ; $1E68 = mask&0xFF     ; select the slot
    $1E66 = addr_hi ; $1E65 = addr_mid
    $1E64 = addr_lo ; $1E67 = replacement
    mask <<= 1
$1E69, $1E68 = trailer PER1, PER0           ; final enable mask
```

So bit0 of the mask is a global enable and bits 1..N enable the slots:
**PER0/PER1 = 2^(N+1) − 1.** The mask is 16-bit, so the ceiling is **15 substitutions** —
three are spent on the existing patch, leaving ample room for our three.
`$0944` holds the entry count; bit7 is set there on any failure.

## 11.5 Consequence for our build

The existing block is `code_len=14, data_len=14, N=3`. To add the sweep we keep its 14 code
bytes exactly where they are (its own hook jumps to `$0EBE`), append our routine after them
so our entry point is `$0EBE + 14 = $0ECC`, add three entries writing `4C CC 0E` over
`$006584-$006586`, set N=6, data_len=26, PER=`$007F`, and recompute H0. All of that is
mechanised in `patchblock.py addhook`; nothing here should ever be done by hand.

---

# 12. The sweep patch — built 2026-08-27

Source `sweep.py` (assembled by `asm65.py`), block assembled by `patchblock.py addhook`.
Artefacts: `sweep_code.bin` (234 bytes), `sweep_block.bin` (280 bytes, the full replacement
for EEPROM `$552`), `eeprom_with_sweep.bin` (simulated post-install image for checking).

## 12.1 What it does

Hooks `$6584` (`JMP ($635F,X)`, the state-machine dispatch) with `JMP $0ECC`. On entry X
already holds state*2, so the replaced instruction is simply re-executed on the way out.
Y is preserved, X untouched, A is scratch — the same contract the reference patch uses.

While the state byte `$86` equals the gate value, it ramps `$0268` (tach) and `$026A`
(speedo) from rest to full scale and back, then sets its phase byte to 3 and never touches
the gauges again. A power cycle reloads the block from EEPROM, which resets the phase byte
and re-arms it — no separate arming flag is needed.

Ramp: 64 steps up + 64 down, +62/-62 per step on the tach and +64/-64 on the speedo, so
neither accumulator can run past its end stop; the exact full-scale values (4026 / 4138) are
written once at the top and the exact rest values (14 / 7) once at the bottom.

## 12.2 Deliberately NOT ported from the reference

| reference does | we don't, because |
|---|---|
| restores two gauges from `$16B0`/`$1720` | we only ever write the two cells we sweep, so there is nothing to restore and no need to find our equivalents |
| clears ZP `$81` bit1 and `$0B2C` bit5 | ROM-specific; no evidence what ours are. First place to look if the sweep runs but something else misbehaves during it |
| rewrites the caller's return address | ROM-specific; without it the dispatched routine just returns normally |
| drives timing from cluster tick `$0CB8` | we don't know our loop rate — see the prescaler below |

## 12.3 Tunables, live-patchable in RAM

We do not know how often the dispatcher runs, so the sweep's duration is unknown until we
watch it. Every timing constant is a named byte in the loaded image, retunable with single
`WriteRAM` writes instead of a rebuild-and-reinstall cycle:

| addr | name | value | meaning |
|---|---|---|---|
| `$0FA9` | GATE | `$0E` | state byte `$86` must equal this |
| `$0FAA` | PRESC | 1 | advance the ramp every Nth dispatch |
| `$0FAB` | STEPS | `$40` | ramp length, each direction |
| `$0FAC` | STEPT | 62 | tach increment per step |
| `$0FAD` | STEPS_ | 64 | speedo increment per step |

Observable state: `$0FAE` PHASE (0 idle, 1 up, 2 down, 3 finished), `$0FAF` PSCNT,
`$0FB0` TICK, `$0FB1/2` tach angle, `$0FB3/4` speedo angle. Reading PHASE and TICK during
the RAM test tells us whether it is running and how fast, without watching the needles.

## 12.4 THE ONE UNVERIFIED ASSUMPTION

`$86 == $0E` was measured **with the engine idling**. It was never measured with the
ignition on and the engine off — which is exactly when the sweep has to run. If `$0E` turns
out to be a running-engine state the sweep would trigger on engine start instead of at
power-on. Cost to settle: one `ReadRAM 0x0086` at the car with ignition on, engine off.
GATE is tunable, so the fix is one byte either way.

## 12.5 Header bug found and fixed during the build

The first build encoded H3 as `total >> 8` = 1 and produced a header that asks the loader
for **530** bytes instead of 274. The loader adds `(H1 & 0x7F) + H2` in **8 bits** and lets
that addition's carry increment H3 (`$F00C-$F019`), so the carry already supplies the high
byte and H3 must not repeat it. Because H1 caps at 127 and H2 at 255, H3 is always 0 and
**the real size ceiling is 382 bytes, set by the header, not the 1590-byte RAM buffer.**
`total_length()` and `header_h3()` now mirror the loader's arithmetic exactly, and the
original installed block still round-trips as a regression check.

## 12.6 Validation of `sweep_block.bin`

Checked independently against every rule the loader enforces: total 274 (even, fits buffer,
fits header), checksum `$55` correct, `H0 != H1`, data length 26 = 6*4+2, PER `$007F` =
2^(6+1)-1 with bit0 set, H4 `$40` matches this ROM. The three factory entries are carried
through byte-identical (`01 6C 77 4C / 01 6C 78 BE / 01 6C 79 0E`) and ours are appended
(`00 65 84 4C / 00 65 85 CC / 00 65 86 0E`). EEPROM `$574-$669`, the space the block grows
into, was verified all `$FF` in the live backup — free.

## 12.7 Order of operations for the RAM test (NOT yet done)

1. Write the 234 code bytes to `$0ECC`. **Code first, always.**
2. Only then arm the module, following the loader's own sequence: for each of slots 4,5,6
   write mask (`$1E69`,`$1E68`), then `$1E66`/`$1E65`/`$1E64` (addr hi/mid/lo) and `$1E67`
   (replacement); masks `$0010`, `$0020`, `$0040`; finally the full mask `$007F`.
   The hook goes live the instant the final mask lands.
3. If the cluster hangs: ignition off, wait, on. Nothing here survives that.

At one byte per `WriteRAM` invocation this is ~254 writes, roughly 45 minutes. Teaching
`WriteRAM` to accept multiple pairs per session — the cluster's `0x87` command already has
a count field, §10.2 — turns that into about a minute, and is now clearly worth the .NET SDK
download.

---

# 13. kw1281test rebuilt, 2026-08-27 — a bug fix and two new commands

Built from source with the .NET 10.0.400 SDK sitting in `dotnet-sdk-10.0.400-osx-arm64/`.
The stock binary is preserved as `kw1281test.orig`; `kw1281test` is now our build.

```
export DOTNET_ROOT="$PWD/dotnet-sdk-10.0.400-osx-arm64"; export PATH="$DOTNET_ROOT:$PATH"
cd kw1281test-master && dotnet publish kw1281test.csproj -c Release -r osx-arm64 \
    --self-contained true /p:PublishSingleFile=true /p:IncludeAllContentForSelfExtract=true \
    -o ./publish-mac
```

## 13.1 The bug: WriteRAM ignored its VALUE argument

In `Program.cs`, `WriteRAM` was grouped with `ReadEeprom`/`ReadRAM`/`ReadROM` in the branch
that parses only `args[4]`. Nothing ever assigned `value`, which is declared and initialised
to 0 at the top of the method, and the dispatch then called `tester.WriteRam(address, value)`.
**Every WriteRAM wrote `0x00`.** The command reported success because the block was accepted
at protocol level — nothing about the output hints that the value was dropped.

Fixed by moving `WriteRAM` into the `WriteEeprom` branch, which parses address *and* value.
This invalidated our own §10.2 conclusion; see the retraction there.

## 13.2 `WriteRamBlock ADDRESS FILENAME`

Writes a file's bytes to consecutive RAM addresses in one session. The cluster's `$87`
custom command already carries a count field — the stock `WriteRam` just hardcoded it to 1,
so a 234-byte patch meant 234 full KWP handshakes, about 40 minutes. Chunked at 32 bytes per
block (the protocol ceiling is ~248; there is no reason to run near it), the same job is
8 round trips.

## 13.3 `WriteRamPairs ADDRESS1 VALUE1 [...]`

Writes address/value pairs **in the order given**, in one session. Needed because the patch
module is a sequencer: the slot-select mask must be written before each slot's address and
data, so an ascending block write would load the wrong slots. `armpatch.py` generates the
exact call.

## 13.4 Still unproven

The multi-byte `$87` write is an inference from the command's count field, not something we
have seen work. `armpatch.py` STEP 2 therefore reads the code back and compares it against
the file before anything is armed. If the count field turns out not to mean what it appears
to, that comparison fails harmlessly and we fall back to one byte per call.

---

# 14. Live RAM test, 2026-08-27 evening — the sweep mechanism, found the hard way

## 14.1 The patch machinery works. All of it.

Loaded 234-244 bytes to `$0ECC` with the new `WriteRamBlock`, verified byte-identical by
read-back, armed slots 3-5 by hand with `WriteRamPairs`, and the hook went live. Our code
executed inside the cluster's main loop, ramped its accumulators, wrote its target cells,
restored `JMP ($635F,X)` on every exit and finished cleanly by setting its phase byte.
The cluster never hung, never faulted, and always took its gauges back afterwards.
Every tunable behaved. **The hook, the loader format, the checksum, the arming sequence and
the code are all proven on the car.**

Measured: the state-machine dispatcher runs **42 times per second**, so at PRESC=1 a
128-step ramp takes 3.0 s. Measured safely with the ramp step set to zero, so no needle
moved during the measurement.

Also confirmed here: `$86 == $0E` with **ignition on and engine off**, identical to idle.
The §12.4 assumption was correct and the gate needs no change.

## 14.2 What was wrong: `$0264-$026B` is a mirror, not a control

Writing the gauge cells does nothing to the needles — not with the engine off, not with it
running, not held for 18 seconds, not with every gauge flag set. The values stick (read back
mid-sweep as 3672/3783 while the needles sat still) and the cluster resumes control after,
so nothing is fighting us. The cells simply are not what the needle driver reads.

They are a faithful *report* of needle position: `$0266` went 67 → 346 as the engine warmed,
`$0268` went 14 → 406 the moment the engine started. That is exactly why §10.1's
identification was correct and yet useless for driving anything.

## 14.3 What is actually the control: four zero-page structs

The driver at bank0 `$56FE-$586C` indexes per-gauge state through the table at `$5387`
(`D4 DF EA F5`) — four 11-byte structs in zero page. ~~Each holds three 24-bit values in
16.8 fixed point (integer position in **motor steps**, plus a fraction byte) at offsets +0,
+3, +6, then two bytes and a mode byte `$18`.~~ **Superseded by §15.1** — the field
boundaries are one byte off. It is a 16-bit demand at +0 followed by two 24-bit filter
stages at +2 and +5, and the byte at +8 is the servo's velocity, not part of a position.
The gauge identification below stands.

Writing 1000 into all three copies of one struct moves that gauge's needle. Identified by
writing each struct in turn and watching:

| struct | gauge | resting value (engine off, cold) |
|---|---|---|
| `$00D4` | **temperature** | 335 steps (cell `$0266` = 67) |
| `$00DF` | **tachometer** | 35 steps (cell `$0268` = 14) |
| `$00EA` | **speedometer** | 28 steps (cell `$026A` = 7) |
| `$00F5` | **fuel** | 725 steps (cell `$026C` = 704) |

Note this also corrects §10.1: `$0264` is **not** the fuel cell. `$0262-$0265` are four
single-byte per-gauge status fields (the driver reads them as `$0262,Y` and masks `#$06`),
and the "fuel = 514" match in the original block search was the two flag bytes `02 02` read
as a word. The four gauge cells are `$0266`, `$0268`, `$026A`, `$026C` — so `$026C` belongs
to the gauge block, not to the `$026C-$0273` timer of §8.1.

~~The step values are not a fixed multiple of the 1/16-degree angles — each gauge has its own
scale — so **full-scale step counts still have to be calibrated per gauge** before the sweep
can drive to the end of each dial. One data point exists: writing 1000 into the speedometer
struct put the needle at roughly 10 km/h.~~ **RETRACTED — see §15.2.** Steps are angle + 21
for every gauge, straight out of `$5BE3`, so every full-scale value was already known. The
10 km/h reading was a needle caught mid-transient, not a steady-state calibration point.

## 14.4 `$0261` is a re-reference command, not a move command

Setting the per-gauge bits from `$538B` (`01 02 04 08`) into `$0261` makes a needle drive to
its zero stop and then seek its true value — the classic stepper re-homing sweep. Observed
directly: temperature and fuel went hard left "as if switched off", then to their correct
mid-dial positions. Useful to know, but it re-seeks the cluster's own value and ignores ours.

## 14.5 Where this leaves the sweep

One change: point the patch at `$00D4/$00DF/$00EA/$00F5` instead of `$0266-$026D`, writing
all three 16.8 copies per gauge. Everything else stands.

Two things to sort out at the bench first — **both done, see §15**:

1. ~~**Calibrate full scale per gauge.**~~ Not needed: steps are angle + 21 (§15.2), so the
   full-scale values were already in our own EEPROM dataset.
2. **Shrink the code.** The experimental build reached 328 bytes and H2 holds the code length
   in **one byte, so 255 is the hard ceiling** (§12.5). Done — 224 bytes, by driving all four
   gauges from one indexed loop (§15.4).

Left the cluster with our slots disabled (`$1E68 = $0F`), i.e. exactly the factory patch.

---

# 15. The gauge servo, read out in full — and the sweep rebuilt on it (bench, 2026-08-27)

> **SUPERSEDED by §17 for everything about the patch.** The servo disassembly in §15.1-15.3
> is still the reference and is what §17 is built on; the patch described in §15.4 onwards
> drew the motion itself and is gone.

§14 found the four zero-page structs by writing to them and watching needles. This section
replaces guesswork with the actual ROM: the whole servo is bank0 `$56FE-$5CF3`, and reading
it settles both open items from §14.5 at once.

## 15.1 The struct is not three copies of a position

§14.3 read the 11 bytes as "three 24-bit values at +0, +3, +6". That was one byte out. The
real layout, from the code that touches each field:

| offset | meaning | evidence |
|---|---|---|
| `+0,+1` | **demand** — where the needle is asked to go, in motor steps | `$5BE3` writes it; `$58B7` compares it against `+6,+7` |
| `+2` | fraction of stage 1 | `$5C04` subtracts it against an implied `.5` |
| `+3,+4` | stage 1, integer steps | `$5C1C-$5C24` |
| `+5` | fraction of stage 2 | `$5C2D` |
| `+6,+7` | stage 2 — **the commanded needle position** | `$5A12` hands its low byte to the coil driver `$5B30` |
| `+8` | current velocity, steps per update | written by `$5CF1`, clamped to ±30 |
| `+9` | unused by the gauge path | |
| `+$0A` | mode; `$18` = running normally | `$58A9` |

`$5C01` is the servo: two cascaded first-order lags, each moving 1/32 of the remaining
error per update (`$5C14`/`$5C41` shift the delta right by 5), with the second stage passed
through a rate limiter at `$5C71` that allows ±30 steps per update and no more than one
step of change in velocity per update.

The live dumps confirm the layout exactly. Coolant at rest read `4F 01 27 4E 01 D2 4C 01 00
00 18` = demand 335, stage 1 at 334.15, stage 2 at 332.82, velocity 0, mode $18 — a needle
quietly chasing a rising temperature. The three static gauges all showed the same fractional
residues (`$61`, `$42`), which is what a converged cascade of identical filters must do and
which made no sense at all under the old reading.

## 15.2 Motor steps = needle angle + 21. Nothing needs calibrating.

`$5BE3` is the routine that loads a demand:

```
$5BE3  PHY / JSR $5F07 / LDX $5387,Y / TYA / ASL A / TAY
       CLC / LDA $0266,Y / ADC #$15 / STA $00,X      ; demand = mirror + 21
       LDA $0267,Y / ADC #$00 / STA $01,X
```

So the "mirror" cells of §10.1 **are** the input after all, and the conversion is a constant
offset of 21. Checked against the car on three gauges at two independent moments:

| gauge | mirror | +21 | demand actually read |
|---|---|---|---|
| tach | 14 | 35 | 35 |
| speedo | 7 | 28 | 28 |
| fuel | 704 | 725 | 725 |

(Coolant is the fourth and agrees too, once the two readings are taken at the same time
rather than an hour apart with the engine warming in between: demand 363 at 15:25 implies a
mirror of 342, and the mirror read 346 two minutes later, still rising.)

The mirror holds a needle angle in 1/16 degree, and our own EEPROM dataset gives every
gauge's full-scale angle. **So every full-scale step count was already known and §14.5's
"calibrate full scale per gauge" is withdrawn — no needle has to be walked up a dial to find
out where it stops.**

| gauge | rest (angle+21) | full scale (angle+21) |
|---|---|---|
| coolant | 88 | 1449 |
| tach | 35 | 4047 |
| speedo | 28 | 4159 |
| fuel | 69 | 1465 |

This also disposes of the one data point that suggested otherwise. "1000 steps ≈ 10 km/h"
would put full scale near 25 000 steps; the curve says 1000 steps is 40 km/h. The needle was
watched during hammered writes from the laptop and never reached steady state — it was a
transient, not a calibration.

## 15.3 Why writing the mirror alone did nothing

Because `$5BE3` runs only when a gauge's state handler decides to reload, not every pass.
Set the mirror and it sits there until something asks for it — which in §14.2 it never did
for eighteen seconds. That is the whole explanation for the day's dead end.

The patch therefore writes the demand **and** both filter stages every dispatch. The demand
keeps the cluster's own reload consistent with us; the two stages put the needle exactly
where we say. That is also the combination proven to move a needle on the car.

One consequence worth stating explicitly: we write the same value into both stages, so the
servo computes a delta of zero and leaves `+8` at zero. That matters. The park routine at
`$5726` reads `+8` when the cluster shuts the gauges down and asks for a **re-reference** if
a gauge is still moving fast — the hard-left-then-back behaviour of §14.4. A sweep that left
a large velocity behind would trigger exactly that.

## 15.4 The rebuilt patch

`sweep.py`, 224 bytes of code and data at `$0ECC`, down from 328. Header `H2` is 238 of the
255 it can hold, block total 264, `sweep_block.bin` 270 bytes at EEPROM `$552-$65F` — inside
the region verified all `$FF` in the live backup.

What made it fit: the four gauges are now driven by one indexed loop instead of four
open-coded blocks. Gauge index lives in Y as `gauge*2`, the struct base comes from a table
at the end of the image, and the struct writes use zero-page-indexed addressing (`STA $03,X`
with X = `$F5`), which is a byte shorter than absolute per store.

The ramp: fixed rest → fixed full scale in `STEPS` equal increments, then the same
increments back. `STEP` is 8.8 fixed point, so the ramp lands within one step of full scale
instead of up to `STEPS` short, and because up and down use the identical increment the
return to rest is exact to the byte. **The endpoint is a build-time constant, so a needle
cannot be driven past full scale whatever the cluster was showing when the sweep began.**

Starting from a fixed rest rather than from the live position is deliberate: at power-on the
cluster has just re-referenced every needle to its zero stop, so rest is where they are.
Capturing the live position would need a divide to rescale the ramp — about 50 bytes — and
H2 has no room for it.

Defaults: 80 ticks each way at the measured 42 Hz = 3.8 s total, worst case ~50 steps
(3.1°) per tick on the tach. Chosen conservatively because a stepper driven faster than it
can follow slips silently and leaves the needle reading wrong until the next power-up; the
cluster's own servo never exceeds 30 steps per update.

Worth stating, because the sweep does park the coolant needle in the red for a moment: the
patch writes **only** the four servo structs. It never touches the `$026x` mirrors, which
are what the overheat warning, the fuel reserve lamp and the odometer's speed input actually
read. Nothing downstream of a gauge sees the sweep — only the needles move.

| addr | tunable | | addr | state |
|---|---|---|---|---|
| `$0F87` | GATE `$0E` | | `$0F92` | PHASE (0 armed, 1 up, 2 down, 3 done) |
| `$0F88` | PRESC 1 | | `$0F93` | PSCNT |
| `$0F89` | STEPS 80 | | `$0F94` | TICK |
| `$0F8A/C/E`, `$0F90` | STEP per gauge, 8.8 | | `$0F95` | POS, four 16-bit positions |

Keep `STEP × STEPS` equal to `(full scale − rest) × 256` when retuning, or the ramp stops
short of the top — or tries to run past it. `python3 sweep.py --steps N` prints the matching
set; `armpatch.py` prints them as ready-made `WriteRAM` arguments.

## 15.5 Verified on the bench, in an emulator

`py65` running the assembled bytes, with the exit vector pointed at an `RTS` so each
dispatch can be called and returned from, and the gauge structs poisoned with `$AA` so any
byte we claim to write has to prove it:

* 80 passes up, 80 down, finished on pass 161 = 3.83 s at 42 Hz.
* Every gauge peaks on exactly the predicted step and returns to exactly its rest value.
* Demand, stage 1 and stage 2 hold the same value on every pass.
* `+9` and `+$0A` still read `$AA` at the end — the mode byte is never touched.
* X and Y come back unchanged from all 180 calls.
* Gate shut: 200 passes, struct untouched, PHASE still 0.
* Gate closing mid-sweep: finishes anyway and returns the needles to rest.
* PHASE preloaded to 3: struct untouched.

This caught a real bug on its first run. The entry test read

```
LDA PHASE / CMP #$03 / BCS done / BNE running
```

where `BNE` was meant as "phase ≠ 0" but actually tested the compare against 3 — so with
PHASE 0 it branched straight past the gate check and the sweep finished on its first pass
without moving anything. Rewritten to test for zero first.

## 15.6 What is left

The RAM test at the car (`python3 armpatch.py` prints the sequence). Everything in §12.7
still applies: code first, arm second, read back before arming, ignition cycle undoes all of
it. Only after that, and only with explicit confirmation, `LoadEeprom 0x552 sweep_block.bin`.

---

# 16. Installed and working, 2026-08-27 evening — four guards, three of them wrong

> **SUPERSEDED by §17.** Kept for the four guards and for §16.2, which is still true and
> still the most useful fact in this file. §16.6's option 4 turned out to be the answer,
> though not the way it is described there: the fix was not a faster hook but handing the
> motion to the servo that was already running at that rate.

The sweep is in EEPROM at `$552-$66F` and plays on every wake. Getting there took four
attempts at one question — *when is it safe to start?* — and each wrong answer was
informative, so all four are recorded.

## 16.1 The three things the ROM actually lets us write

Settled by direct experiment at the car, and it disposes of §14.2's open question:

| written | what happened |
|---|---|
| demand `+0,+1` alone | restored to `mirror + 21` within **2 s** |
| mirror `$0268` alone | restored by the measurement chain within **4 s** |
| both filter stages | ours, and the needle follows |

So the mirror and the demand both belong to the cluster and are rewritten continuously.
Only the filter stages are ours. §14.2's mirror writes were not ignored — they were
overwritten before anything read them. The patch writes the demand as well, but only to
keep the servo's velocity byte at zero, not because it moves anything.

## 16.2 The ignition key does not reset this cluster

The first install looked like a total failure: after an ignition cycle `$6584` still read
`7C 5F 63` and `$0944` said 3 entries. Nothing was wrong with the block. The cluster sits
on permanent power for the clock and odometer, so **KL15 off/on leaves the CPU running** —
the patch loader never ran. Proof: the patch's own state byte and the runtime variables at
`$1000` survived the cycle intact.

The user pointed out that kw1281test already has `Reset`. With a real reset the loader ran,
`$6584` read `4C CC 0E`, `$0944` read 6, no error flag. That command turned a
five-minute test cycle into a ten-second one and is the only reason the rest of this
section exists. A long KL15-off (a minute or more) lets the cluster sleep and does reset
it, which is what makes the feature work in normal use.

## 16.3 Four guards

The sweep must not start until the cluster has finished re-referencing its needles, or it
captures a position that is not where the needle is.

| guard | result on the car |
|---|---|
| none — fixed rest constants | fuel was 660 steps from the constant; the stepper cannot follow a jump that big, it re-synced to the nearest phase and everything after ran out of register until the needle jammed. **This is what put a needle into its end stop.** |
| capture the live position | correct while running, but at boot it captured **0** on all four — needles only twitched |
| mode byte == `$18` | no good: `$57EE` sets mode `$18` in the same breath as zeroing the demand, so it is already set during referencing. Captured **1** |
| demand >= `$14` | no good either: referencing walks all four demands upward together and we caught them all at **21**. No threshold can work — the speedometer's genuine rest (28) is lower than values the others pass through |
| **DLY: count 255 dispatches after the gate opens** | tach 35, speedo 28 exactly; coolant 81 and fuel 646 still climbing towards 88 and 729, close enough that no step is lost. **All four sweep the full scale.** |

`DEC DLY / BNE OUT0` is five bytes, one fewer than the threshold test it replaced, and it
cannot run twice because PHASE moves to 1 on the pass where it reaches zero.

## 16.4 The needle speed limit is electrical, not mechanical

At SHIFT 6 (64 ticks each way, 3.0 s) the speedometer **did not move at all** while the
tachometer, one step per tick slower, was fine. That is too sharp a boundary for friction.
The coil driver takes the low byte of the position (`$5A12` → `$5B30`), so the electrical
phase is position mod 256 and a step of 64 is exactly a quarter cycle — a jump that size
leaves the rotor no preferred direction. The cluster's own ±30 clamp at `$5C71` is not
caution, it is the real limit. SHIFT 7 (31-32 steps per tick, 163 deg/s) works.

## 16.5 As installed

Block `$552-$66F`, 286 bytes, code 240, H2 254 of 255. Read back byte-identical, and
**not one byte outside the block changed** across three installs. `restore_block.bin` is
the original 34-byte factory block; `LoadEeprom 0x552 restore_block.bin` undoes everything.

Timeline from a wake: cluster references its needles, our 6.1 s delay, 6.1 s sweep.

| addr | | addr | |
|---|---|---|---|
| `$0FA6` | GATE `$0E` | `$0FA9` | PHASE |
| `$0FA7` | SHIFT 7 | `$0FAA` | DLY 255 |
| `$0FA8` | STEPS 128 | `$1000` | POS, STEP, TICK (outside the block) |

Re-arm for a repeat without a reset: `WriteRAM $0FA9 0` — but also reload DLY, since it is
left at 0 and `DEC` would wrap it to 255.

## 16.6 Making it quicker — the options, with numbers

1. **Trim DLY.** 6.1 s is the maximum a byte can hold and was chosen blind. The tach and
   speedo were fully settled by then; measuring when they actually settle (poll the demand
   once a second after a `Reset`) would likely recover 3-4 s. No code change, no risk.
2. **A ramp divisor between 64 and 128.** `STEP = (D>>7) + (D>>8)` is D×3/256, i.e. 85
   ticks = 2.0 s each way, speedo step ~49. That is in the untested gap between 32 (proven
   good) and 64 (proven bad), so it needs a careful RAM test. Costs ~15 bytes, which means
   making GATE/SHIFT/STEPS compile-time constants to free them.
3. **An adaptive readiness test** — two consecutive equal demand readings instead of a
   fixed wait. Starts the sweep the moment the gauges settle. ~10 bytes.
4. **A faster hook.** Everything above is boxed in by the 42 Hz dispatch rate: at 30 steps
   per update that is 78 deg/s and a full-scale sweep can never be quicker than about 3 s
   each way. A hook on a call site that runs at, say, 200 Hz would allow small steps taken
   often — full scale in well under a second, and smoother. This needs ROM work to find a
   suitable site and a fresh RAM test, and it is the only option that changes the ceiling
   rather than trimming around it.


---

# 17. Rebuilt on the cluster's own servo, 2026-08-28 — installed and measured

The build of §15-§16 drew the needle motion itself, one step per dispatch. This one hands
the endpoints to the ROM's servo and lets it draw them. It is 5x faster end to end, it
cannot jam a needle, and the code shrank from 240 bytes to 202 even after everything below
was added.

The trigger was a video of a Golf 4 sweep and the realisation that the reference patches in
`reference/` — which had been sitting in the repo unread — answer the question outright.

## 17.1 What the reference patches actually do

All five of `reference/NeedleSweep-*.bin` are the same 158 bytes with different addresses.
The whole sweep is three thresholds on a counter the ROM already maintains:

```
CPY #$03 / BCC ...          ; Y = $0CB9, a tick counter
CPY #$0E / BCS ...
    LDA #$CF / STA $026E    ; = $0FCF = 4047
    LDA #$F1 / STA $0270    ; = $0FF1 = 4081
    LDA #$0F / STA $0271 / STA $026F
CPY #$1B / BCS out
    STZ $026E / STZ $026F / STZ $026C / STZ $0271
```

**No interpolation at all.** It writes the mirror cells and leaves; the servo does the rest.
Two things fall out of this:

* `$0FCF` = 4047 is bit-for-bit our own tachometer's full-scale demand (4026 + 21). The
  same value in a patch for a different ROM is independent confirmation that both the
  1/16-degree unit and the `+21` offset of §15.2 are right.
* Only two needles move because the patch keeps feeding the other two mirrors from live
  sensor registers (`$1696`, `$1706`) while it sweeps. We get the same effect for free by
  simply not touching them.

The reference also hooks the **gauge-mode dispatcher** (`$55DA`, our `$5493`), not the main
loop, and Golf `$0B2E` is our `$0980` — the `LDA #$20 / TRB` pair matches our `$5531` byte
for byte.

## 17.2 Why the old build could never be quick

The servo runs in an interrupt: bank1 vector `$FFC6` -> `$52F6` (`CLD/PHA/PHX/PHY` ...
`RTI`) -> `JSR $5464`, and `$54AC` cycles `$7F` over 0..3 so **one gauge is serviced per
interrupt**. The foreground loop at `$6575` is free-running and was measured at 42 Hz. The
rate limiter is `$5CC3: CMP #$1E` — 30 steps per servo update, plus one step of velocity
change per update.

So the ceiling for anything driven from the foreground hook is 30 x 42 = 1260 steps/s =
79 deg/s, and §16.4's failure at 64 steps per dispatch was us trying to beat it in one jump.
The servo simply takes its 30 steps several times more often. Measured on this car once the
demand fight was settled: **294 deg/s**, against 84 deg/s for the old build and 330 deg/s
measured off the Golf video frame by frame.

## 17.3 What this build writes

Per dispatch, tachometer and speedometer only, bracketed by the ROM's own `$5F07`/`$5EF9`
interrupt gate:

| | |
|---|---|
| `$0266+2g` | mirror = target angle |
| `+0,+1` | demand = target + 21, computed `$5BE3`'s way |
| `+3,+4` | **filter stage 1**, the same value again |
| `+$0A` | `$18`, so the servo keeps servoing |
| `$0261` | our two bits set, cleared once when the sweep ends |

**Stage 2 (`+5,+6,+7`) is never written.** That is the whole safety argument: every step the
coil driver sees still came out of the factory rate limiter, so no write of ours can
desynchronise the electrical phase or outrun the stepper. §16.3's end-stop jam is impossible
by construction, and the emulator confirms no needle is ever driven past full scale or below
rest at any servo rate.

## 17.4 The tug-of-war, and the two things that settle it

Writing only the mirror and the demand does not work, and the car said so immediately: the
needles rose about 40 degrees, sagged, rose again, and sawtoothed like that for the whole
sweep. §16.1 had measured a *single* write surviving 2 s; that is not the same as how often
the reload runs, which is about as often as we write.

**Stage 1** is the first answer. It is the one field in the struct that only the servo
itself ever writes. Held at the target, it decays towards whatever the cluster puts in the
demand by only 1/32 of the error per update, and stage 2 spends the climb chasing a target
thousands of steps away with its rate limiter saturated. That alone made the motion clean.

**`$0261` is the second.** `$5790` reads it, ands it with the gauge's bit from `$538B`
(`01 02 04 08`), and *skips* the `JSR $5BE3` reload when the bit is set — it is the
cluster's own "a re-reference is pending, do not trust the mirror" flag. Setting our two
bits for the length of the sweep stops the fight at the source. The gain was larger than
expected: the climb went from 1.12 s to 0.81 s, i.e. the fight had been costing a third of
the speed as well as the last 10 degrees of travel.

The bits **must** be cleared when the sweep ends. Left set, the cluster runs its reference
walk and both needles visibly dip to the stop and back. The patch clears them on the first
dispatch after the sweep, exactly once, using `PH` as the marker.

An audit found `$0261` touched at eight sites in bank0 and read as a condition at only one,
`$5790`. Verified on the car: `$0261` reads `$00` after every sweep.

## 17.5 Starting without the long wait

§16.3's 255-dispatch delay existed because the old build captured a live position and had to
be sure the capture was real. This one captures nothing, so the gate is an honest readiness
test: both gauges must have finished the coil-alignment chain
(`$04`->`$06`->`$08`->`$10`->`$12`->`$14`->`$16`->`$18`, handlers `$5917`-`$59DF`), i.e.
mode >= `$18`. Holding mode `$18` through the sweep also overrides the slow part of the
cluster's own startup: `$569B` sends a gauge that is near zero into mode `$1A`, and
`$5A24`/`$5A45` walk it to the stop and back one step every 18 gauge-ticks.

A cold start could not be faked. Writing mode `$04` by hand does run the alignment chain,
but it ends in mode `$00` (`$59C8` zeroes the demand and sets mode 0) and a different part
of the state machine promotes a gauge from there to `$18` — so the gauges were left at the
stop and had to be recovered with `WriteRamPairs 0x00E9 0x16 0x00F4 0x16`. The gate itself
behaved correctly throughout (`T` stayed 0). The real proof came from the EEPROM install
and a `Reset`.

## 17.6 The turn is the needle's decision, not a counter

How many servo updates happen per dispatch is set by the interrupt period, which is not in
any bank we hold, so a fixed climb length would be a guess. The patch instead turns round
when the speedometer reports it has arrived — stage 2 above 94% of full scale — then holds
`DWELL` dispatches so the needle creeps the last degree in, which is also the visible pause
at the top. `T2` remains a timeout for a blocked needle.

A velocity term was tried in that test and had to be removed: it fired on exactly one
dispatch out of ninety-six, because the tug-of-war kept the velocity byte hunting, so the
needle sat at the stop waiting out the whole timeout. That is what the car reported as
"stood at the top rather too long".

`DWELL` was chosen from the emulator: 12 dispatches leaves 0.9-5.8 degrees unswept, 20
leaves 0.1-2.8, 28 leaves almost nothing but is 0.67 s of standing. 20 it is — 0.48 s,
close to the 0.35 s the Golf holds.

## 17.7 Verification

`emu_sweep.py` maps bank0 at `$2000-$7FFF` and calls the **real `$5C01`** out of the same
ROM image between dispatches, so the trajectories it prints are the ROM's own arithmetic and
not a model of it. `--from-eeprom` takes the code back out of the built block, so what is
tested is the bytes that get flashed. Ten checks, all passing: registers and stack preserved
across every dispatch; coolant and fuel untouched in mirror, demand and needle; `+9` still
poisoned; both needles reach full scale and return exactly to rest, stopped (`|v| < 5`, or
`$5726` would ask for a re-reference at park); nothing moves with the gate shut or the coils
still aligning; a late-arriving gauge still sweeps in full; no needle ever past full scale or
below rest at any servo rate; the sweep beats the cluster rewriting mirror and demand on
*every* dispatch; and finished is final until `WriteRAM T 0`.

## 17.8 As installed

Block `$552-$649`, 248 bytes, code 216 (202 ours + 14 factory), H2 `$D8` of `$FF`. Read back
byte-identical, and nothing outside the block changed. `restore_block.bin` still reverts
everything. The install file is padded to 286 bytes with `$FF` so one `LoadEeprom` also
clears the tail of the old, longer block.

From a real `Reset`: gate opens, arrival on dispatch 34 (0.81 s), turn on 54 (1.29 s),
finished on 126 (3.00 s), both needles exactly at rest with velocity 0 and mode `$18`.
Against roughly 12 s for the old build — 6.1 s of waiting and 6.1 s of sweep.

## 17.9 Not done

The `$5CC3 CMP #$1E` clamp could be substituted through the patch module (9 free slots) to
raise the servo's own limit. It would apply to all four gauges all the time, including
normal driving, and 294 deg/s is already close to what the X27.168 is rated for. Deliberately
left alone.

## 17.10 The last twitch is the factory's, not ours

The car reported that the speedometer "does not quite come all the way down, and twitches
downward when you hand control back". Three measurements closed it, in this order.

**The cluster's own park position is 28.** Read straight off its mirror with nothing of ours
running: `$026A` = 7, so demand = 7 + 21 = 28, and stage 2 sat at exactly 28. That is the
value our down phase already commands, so there was never anywhere further for the needle to
go.

**Nothing moves at handover.** A `SNAP` word was added to the patch: on the finish pass,
before it clears `$0261`, it copies the speedometer's stage 2 into `$0F93`. It reads **28**, and
the needle reads 28 afterwards. Zero steps of commanded movement across the handover. So the
first theory — that our `DOWN` window ended before the servo had settled — was wrong, and
lengthening `DOWN` from 72 to 130 changed nothing, which is what the car said too.

**It is the holding current.** `$5B30` ends with

```
$5B6B  LDA $7E
$5B6D  BEQ $5B7D
$5B6F  LDA $0104,X / LSR A / STA $0104,X    ; halve one coil
$5B76  LDA $0103,X / LSR A / STA $0103,X    ; halve the other
```

and `$7E` is set at `$5A0E` only when the velocity byte is zero **and** stage 2 equals the
demand in both bytes (and `$097D` = 1). So the moment a needle arrives and stops, the cluster
halves both coil currents to save power, and the needle relaxes into the nearest magnetic
detent. It happens at the end of *any* needle movement on a stock cluster; the sweep is
simply the one time somebody is watching that needle at that exact instant.

Nothing to fix. Keeping it from firing would mean holding a gauge permanently one step away
from its demand at full coil current, which is worse in every way.

Also settled on the way past: the video showed both needles apparently creeping downward for
seconds after the sweep, which looked like a slow tail. That was the camera — the backlight
and exposure settle over the same window and drag the measured needle centroid with them.
The wire says the needles are at 35 and 28 with velocity 0 and stay there.

## 17.11 As installed, final

Block `$552-$657`, 262 bytes, code 230 (216 ours + 14 factory), H2 `$E6` of `$FF`. Shape:
arm 3, climb until the speedometer arrives (timeout 99), hold `DWELL` 20 at the stop, then
`DOWN` 72 coming home. From a real `Reset`: arrival on dispatch 34 (0.81 s), turn on 54
(1.29 s), finished on 126 (3.00 s), `SNAP` 28, `$0261` back to `$00`, both needles exactly at
rest with velocity 0 and mode `$18`.

`DOWN` had been raised to 130 and then 96 while chasing the twitch of §17.10. Once `SNAP`
showed the needle was already home at handover, it went back to 72 and `SNAP` still reads 28
on the car — so the settle window was never the problem and the 0.57 s is given back.

## 17.12 Parking a needle at a known value — and what it said about the mounting

The `TGT` table lives in the loaded RAM image, so any endpoint can be changed with two
WriteRAM writes and no rebuild. That turns the patch into a needle-calibration rig: set a
gauge's target to the cell value for a known reading, replay, and the needle parks there and
holds for the rest of the sweep.

| | |
|---|---|
| `$0F97` | tachometer, full-scale target |
| `$0F99` | speedometer, full-scale target |
| `$0F9B` | tachometer, rest target |
| `$0F9D` | speedometer, rest target |

Set a gauge's *up* and *rest* targets to the same value and it goes there at the start of the
sweep and stays. `T2` and `DOWN` set how long: keep `T2 + DOWN <= 255`, because the patch
computes `T3 = T + DOWN` at the turn and a byte overflow there ends the sweep instantly. With
`T2` 60 and `DOWN` 195 the hold is about 5.8 s, which is the practical maximum.

`python3 gaugecal.py predict eeprom_decrypted.bin rpm 3000` gives the cell value; remember the
struct holds that **plus 21**.

Done for the tachometer at 3000 rpm (cell 1515 = `$05EB`, angle 94.69 deg). The struct read
back demand 1536, stage 2 1536, velocity 0 — **the shaft was exactly where it was commanded,
to the step**. The needle did not line up with the 3000 mark, which puts the error entirely
between the shaft and the needle: it is pressed on crooked. Same conclusion the speedometer
had already suggested in §17.10, and it is worth knowing that neither is a fault of the
cluster's electronics, the dataset curves, or this patch.

Useful constants for judging it: the tachometer curve is exactly linear at 510 cells per
1000 rpm, so **1 degree = 31 rpm**. The speedometer is not linear -- about 1.7 deg/km-h near
zero, 1.1 deg/km-h at 100 -- so the same angular error reads as roughly 3 km/h at the bottom
of the dial and 4.5 km/h at 100.

Two cautions when judging a needle by eye. Photograph or look at it square on: the needle
stands proud of the dial face and an off-axis view adds several degrees of parallax by
itself. And a `Reset` restores every one of these RAM edits from EEPROM, so there is no
cleanup to do afterwards -- which was verified: targets back to 4026 / 4138 / 14 / 7,
`T2` 99, `DOWN` 72, `$0261` `$00`, every gauge at rest.

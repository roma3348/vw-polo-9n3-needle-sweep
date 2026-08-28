#!/usr/bin/env python3
"""
Build the needle-sweep patch code for VQMJ07HH-08.40.

REWRITTEN 2026-08-28 around the cluster's own servo. The previous build drew the motion
itself, one step per dispatch; this one hands the endpoints to the ROM and lets the servo
draw it. See PATCH_ENGINEERING.md and the summary below.

WHY THE OLD DESIGN WAS SLOW, AND WHY IT COULD JAM A NEEDLE

Each gauge owns an 11-byte struct in zero page (table at bank0 $5387 = D4 DF EA F5):

    +0,+1   demand: where the needle is asked to go, in motor steps (16-bit)
    +2      fraction of stage 1        \\  two cascaded first-order lags, each of which
    +3,+4   stage 1, integer steps      >  moves 1/32 of the remaining error per update
    +5      fraction of stage 2        /
    +6,+7   stage 2 -- the commanded needle position; $5A12 hands its low byte to the
                       coil driver $5B30, so the electrical phase is position mod 256
    +8      velocity, clamped to +/-30 steps per update by $5C71 ($5CC3: CMP #$1E),
            and to one step of change in velocity per update
    +$0A    mode; $18 = running the servo

The old patch wrote +3..+7 directly, i.e. it bypassed the servo and became the servo. That
capped it at the dispatch rate: 42 Hz x 31 steps = 1344 steps/s = 84 deg/s, and pushing to
64 steps per dispatch broke the speedometer outright because 64 is a quarter of the
256-step electrical cycle and leaves the rotor no preferred direction. It is also what put
the fuel needle into its end stop: a big jump in +6 is a jump the stepper cannot follow.

The servo does not have that problem. It runs in the interrupt -- bank1 vector $FFC6 ->
$52F6 -> JSR $5464, and $54AC round-robins $7F over 0..3, so one gauge is serviced per
interrupt -- which is several times faster than the 42 Hz foreground loop at $6575. Its
+/-30 clamp is not caution, it is the motor's real limit, and it never takes a step the
rotor cannot follow. Measured off the reference patch's own sweep on video: ~330 deg/s.

SO THIS BUILD WRITES ONLY THE ENDPOINTS

Per dispatch, for the tachometer and the speedometer only:

    mirror  $0266+2g = target angle       (what the cluster reloads the demand from)
    demand  +0,+1    = target angle + 21  ($5BE3's own rule, computed the same way here)
    mode    +$0A     = $18                (so the servo keeps servoing while we drive it)

    stage 1 +3,+4    = the same value again

Writing the demand alone is not enough, and this was measured on the car: with only the
mirror and the demand written, the needles rose about 40 degrees, sagged, rose again, and
sawtoothed like that for the whole sweep. The cluster reloads the demand from the mirror
about as often as we write it (PATCH_ENGINEERING.md §16.1: demand restored within 2 s,
mirror within 4 s -- but that is how long a *single* write survives, not how often the
reload runs), so the servo spent the sweep alternating between our target and the true
reading and made almost no net progress.

There is also a switch for it in the ROM. $5790 reads $0261, ands it with the gauge's bit
from $538B, and *skips* the JSR $5BE3 reload when the bit is set -- that register is the
cluster's own "a re-reference is pending, do not trust the mirror" flag. Setting our two
bits for the length of the sweep stops the tug-of-war at the source. They are cleared again
on the first dispatch after the sweep ends, which matters: left set, the cluster would run
its reference walk and the needles would visibly dip to the stop and back.

Stage 1 is the second half of the answer, and it is kept because it costs four bytes and
covers anything in the measurement chain that this does not. It is the one field in the struct that only the servo itself ever
writes -- neither the measurement chain nor $5BE3 touches it. Hold stage 1 at the target and
the cluster can put whatever it likes in the demand: stage 1 decays towards it by only 1/32
of the error per update, we restore it 42 times a second, and stage 2 spends the whole time
chasing a target thousands of steps away, which means its rate limiter stays saturated at
the full 30 steps per update.

The mirror and the demand are still written, so that when we let go nothing lurches. The
writes are bracketed by the ROM's own $5F07/$5EF9 interrupt gate so the servo can never read
a half-written 16-bit value.

Stage 2 -- +5,+6,+7, the number the coil driver actually consumes -- is never written. That
is what keeps every step the motor takes under the factory rate limiter, and it is why no
write of ours can desynchronise the coil phase or outrun the stepper. The whole failure mode
of the old build is gone by construction. Coolant and fuel are not touched at all, which is what makes only two needles
move, and it costs nothing: their normal measurement chain keeps running.

STARTING WITHOUT THE LONG WAIT

The old build sat out 255 dispatches (6.1 s) because it captured a live position and had to
be sure the capture was real. This one captures nothing, so the gate can be an honest
readiness test instead of a clock: both our gauges must have finished their coil-alignment
chain ($04 -> $06 -> $08 -> $10 -> $12 -> $14 -> $16 -> $18, handlers at $5917-$59DF), i.e.
mode >= $18.

That skips the expensive part of the wait. $569B decides, per gauge, between "far from zero:
demand = 0, mode $18" (the servo does it, fast) and "already near zero: mode $1A", and modes
$1A/$1C ($5A24/$5A45) walk the needle to its stop and back to the zero mark one step every
18 gauge-ticks -- ~42 steps, seconds of it. Holding mode $18 through the sweep overrides
that for our two gauges; the cluster redoes it afterwards if it still wants to, and 21 steps
is 1.3 degrees, which nobody sees.

TIMING

T counts dispatches from the moment the gauges are ready. T1 starts the up phase, T2 turns
it round, T3 ends it for good. All three are single bytes in the loaded image, so the whole
shape is retunable at the car with three WriteRAM writes and no rebuild.

The one number not knowable from the dumps is how many servo updates happen per dispatch --
that needs the interrupt period, which is not in the ROM we hold. UP and DOWN are therefore
set for the pessimistic end of the range; emu_sweep.py prints the sweep for each plausible
value, and one measurement at the car pins it. Being generous costs dwell at the endpoints,
never a lost step.

Usage: python3 sweep.py [--up N] [--down N] [--arm N] [out.bin] [entry_addr]
"""
import sys

from asm65 import assemble

# $5BE3: demand = mirror + $15. The mirror holds a needle angle in 1/16 degree.
ZERO_OFFSET = 21

# Only these two sweep. (name, gauge index, struct base, rest angle, full-scale angle);
# angles are this cluster's own, read from eeprom_decrypted.bin by `gaugecal.py curves`.
SWEEP = [
    ("tach",   1, 0xDF, 14, 4026),
    ("speedo", 2, 0xEA, 7,  4138),
]
# Left alone, so their needles stay put and keep reading the truth.
UNTOUCHED = [("coolant", 0, 0xD4), ("fuel", 3, 0xF5)]

ARM = 3     # dispatches to hold after the gauges report ready, before moving
UP = 96     # timeout on the up phase; normally the needle's own arrival ends it
DOWN = 72   # dispatches spent commanding rest after the turn. Generous on purpose: the
            # needle is home long before it expires, holding rest costs nothing visually,
            # and $5726 asks for a re-reference if |velocity| >= 5 when the cluster parks
            # the gauges, so letting go early is the only thing that could bite.

# Fraction of full scale that counts as "the needle has arrived", as a high byte. It has to
# sit low enough that the needle reaches it even when the cluster is fighting hardest over
# the demand -- under a 50/50 fight stage 2 settles on the average of stage 1's sawtooth
# rather than its peak, a couple of degrees short -- and high enough to be at the stop.
ARRIVE_AT = 0.94
DWELL = 20  # dispatches held at full scale after the needle first reports arrived, so it
            # creeps the last degree or two into the stop instead of turning the moment the
            # threshold is crossed. Also the visible pause at the top of the sweep.

GATE_STATE = 0x0E   # cluster state byte $86; measured $0E at ignition-on and at idle

FACTORY_CODE = 14   # bytes of the existing patch, which sit ahead of ours in the block
DATA_SECTION = 26   # 6 module entries of 4 bytes + the 2-byte PER trailer
H2_MAX = 0xFF       # the header holds the code length in one byte

SOURCE = """
; ---- entry: reached from the patched JMP at $6584, X = state*2 ----
ENTRY:  PHX                  ; X selects the vector we re-execute on the way out
        PHY
        LDA T
        CMP T3
        BCC LIVE             ; the sweep is still running
        LDA PH               ; it is over. Give the demand back to the cluster, once.
        BEQ OUT
        LDX #${speedo_base:02X}   ; but first record where the needle actually is at the
        LDA $06,X            ; instant we let go -- the only way to tell a sweep that
        STA SNAP             ; stopped short from one that had already settled
        LDA $07,X
        STA SNAP+1
        LDA #${bits:02X}
        TRB $0261
        STZ PH
        BRA OUT
LIVE:   CMP T1
        BCS GO               ; already sweeping -- the gate no longer matters

; ---- arming: run only while both our gauges are still getting ready ----
; No position is captured, so there is nothing here that can be captured wrong. The only
; question is whether the servo is live yet, and the mode byte answers it exactly: every
; mode below $18 is part of the coil-alignment chain, during which the handlers drive the
; coils themselves and a demand we wrote would be ignored -- or worse, forcing mode $18
; mid-alignment would abandon it. $18, $1A and $1C all mean the coils are aligned.
        LDA $86
        CMP GATE
        BNE OUT
        LDX #${tach_base:02X}
        LDA $0A,X
        CMP #$18
        BCC OUT              ; tachometer still aligning
        LDX #${speedo_base:02X}
        LDA $0A,X
        CMP #$18
        BCC OUT              ; speedometer still aligning
        STZ PH               ; so that writing T back to 0 replays the whole sweep
        LDA DWELL
        STA DW
        INC T                ; ready: start the clock, but move nothing this pass
OUT:    JMP EXIT             ; the far end of the routine is out of branch range

; ---- sweeping ----
; The up phase ends when the needle has actually arrived, not when a counter says it should
; have. How many servo updates happen per dispatch is set by the interrupt period, which is
; not in any bank we hold, so a fixed count would be a guess: too few and the needle turns
; round short of the stop, too many and the sweep drags. The speedometer has the longer
; travel of the two, so once it has arrived the tachometer has too. "Arrived" is position
; alone: a velocity term was tried on the car and fired on exactly one dispatch out of
; ninety-six, because the cluster's tug-of-war over the demand keeps the velocity byte
; hunting -- so the needle sat at the stop waiting out the whole timeout. T2 stays as a
; timeout in case a needle is blocked and never arrives at all.
GO:     LDA PH
        BNE MOVE             ; already coming back; that decision is final
        LDX #${speedo_base:02X}
        LDA $07,X            ; stage 2, high byte
        CMP #${speedo_hi:02X}
        BCC LATE             ; not up at the stop yet
        DEC DW               ; arrived: hold here for DWELL dispatches, letting the needle
        BNE MOVE             ; creep the last degree in, then turn
        BRA TURN
LATE:   LDA T
        CMP T2
        BCC MOVE             ; not arrived and not timed out: keep climbing
TURN:   LDA #$04             ; PH = table offset: 0 going up, 4 coming back
        STA PH
        CLC
        LDA T
        ADC DOWN
        STA T3               ; and come home for DOWN dispatches from this one

MOVE:   INC T
        JSR $5F07            ; interrupts off -- the ROM's own gate, so the servo in the
                             ; interrupt can never read a half-written 16-bit demand
        LDA #${bits:02X}     ; and stop $5790 reloading the demand from the mirror behind
        TSB $0261            ; us: with the bit set for a gauge it skips its JSR $5BE3
        LDY #$04             ; Y = gauge*2: $04 speedometer, then $02 tachometer
WLOOP:  TYA
        CLC
        ADC PH
        TAX                  ; X = target index: 2,4 going up / 6,8 coming back
        LDA TGT,X
        STA $0266,Y          ; mirror low -- what the cluster reloads the demand from
        CLC
        ADC #$15             ; and the demand is mirror + 21, computed $5BE3's way so the
        PHA                  ; two can never disagree
        LDA TGT+1,X
        STA $0267,Y          ; mirror high
        ADC #$00
        LDX GB,Y             ; X = struct base
        STA $01,X            ; demand high
        STA $04,X            ; and filter stage 1, which is the one place the cluster
        PLA                  ; never writes -- see the note above
        STA $00,X            ; demand low
        STA $03,X
        LDA #$18
        STA $0A,X            ; hold the gauge in "running" so the servo keeps servoing,
                             ; which is also what overrides the slow $1A/$1C stop-walk
        DEY
        DEY
        CPY #$02
        BCS WLOOP
        JSR $5EF9            ; interrupts on

EXIT:   PLY
        PLX
        JMP ($635F,X)        ; the instruction we replaced

; ---- tunables: patchable in RAM with single WriteRAM writes ----
GATE:   .byte ${gate:02X}    ; cluster state the sweep is allowed to start in
T1:     .byte {t1}           ; dispatches held after the gauges report ready
T2:     .byte {t2}           ; latest dispatch the sweep may still be climbing on
DOWN:   .byte {down}         ; dispatches spent coming home, counted from the turn
DWELL:  .byte {dwell}        ; dispatches held at the stop once the needle reports arrived
DW:     .byte {dwell}        ; that countdown, reloaded whenever the sweep re-arms
T3:     .byte {t3}           ; dispatch the sweep ends on; rewritten at the turn
T:      .byte 0              ; dispatches counted so far; write 0 to replay without a reset
PH:     .byte 0              ; which half of TGT is live: 0 climbing, 4 coming home
SNAP:   .word 0              ; speedometer stage 2 on the dispatch we handed back

; ---- constants ----
TGT:    .byte $00,$00        ; +0,+1 are never indexed; Y is 2 or 4 and PH is 0 or 4
        .word {tach_top}     ; +2  tachometer, full scale
        .word {speedo_top}   ; +4  speedometer, full scale
        .word {tach_rest}    ; +6  tachometer, rest
        .word {speedo_rest}  ; +8  speedometer, rest
GB:     .byte $00,$00,${tach_base:02X},$00,${speedo_base:02X}
"""


def build(arm=ARM, up=UP, down=DOWN, entry=0x0ECC):
    """Assemble for a given sweep shape. Returns (code, labels, timing)."""
    t1, t2, t3 = arm, arm + up, arm + up + down
    if t3 > 0xFF:
        raise SystemExit(f"arm+up+down = {t3} does not fit in a byte")

    by_name = {name: (idx, base, zero, full) for name, idx, base, zero, full in SWEEP}
    fmt = {"gate": GATE_STATE, "t1": t1, "t2": t2, "t3": t3,
           "down": down, "dwell": DWELL,
           # $538B = 01 02 04 08, one bit per gauge, tested at $5790
           "bits": sum(1 << idx for _n, idx, _b, _z, _f in SWEEP)}
    for name in ("tach", "speedo"):
        _, base, zero, full = by_name[name]
        fmt[f"{name}_base"] = base
        fmt[f"{name}_rest"] = zero
        fmt[f"{name}_top"] = full
        # high byte of the demand at full scale: the "has it arrived?" threshold
        fmt[f"{name}_hi"] = int((full + ZERO_OFFSET) * ARRIVE_AT) >> 8

    code, labels = assemble(SOURCE.format(**fmt), entry)
    # The loader halves (code_len + data_len) to get a word count and rejects an odd total
    # ($F03F-$F043), so pad by a byte when the block would come out odd. Never executed.
    pad = (FACTORY_CODE + len(code) + DATA_SECTION) & 1
    return code + bytes(pad), labels, (t1, t2, t3)


def main():
    args = sys.argv[1:]
    arm, up, down = ARM, UP, DOWN
    while args and args[0].startswith("--"):
        opt, val, args = args[0], args[1], args[2:]
        if opt == "--arm":
            arm = int(val, 0)
        elif opt == "--up":
            up = int(val, 0)
        elif opt == "--down":
            down = int(val, 0)
        else:
            raise SystemExit(f"unknown option {opt}")
    out = args[0] if args else "sweep_code.bin"
    entry = int(args[1], 0) if len(args) > 1 else 0x0ECC

    code, labels, (t1, t2, t3) = build(arm, up, down, entry)
    open(out, "wb").write(code)

    code_len = FACTORY_CODE + len(code)
    total = code_len + DATA_SECTION
    print(f"entry point   ${entry:04X}")
    print(f"code length   {len(code)} bytes  (${entry:04X}-${entry + len(code) - 1:04X})")
    print(f"written to    {out}")
    print(f"header H2     {code_len} of {H2_MAX} "
          f"({'OK' if code_len <= H2_MAX else 'TOO BIG'}), block total {total}"
          f"{'' if total % 2 == 0 else ' -- ODD, the loader rejects this'}\n")

    print(f"shape: arm {t1} dispatches, then climb until the speedometer arrives "
          f"(timeout {t2}),")
    print(f"       then {down} dispatches coming home. "
          f"Worst case {t3} dispatches = {t3 / 42:.2f} s at 42 Hz;")
    print(f"       normally the turn is much earlier -- emu_sweep.py prints where.\n")

    print(f"{'gauge':9} {'struct':7} {'rest':>12} {'full scale':>14}   travel")
    for name, _, base, zero, full in SWEEP:
        print(f"{name:9} ${base:02X}     "
              f"{zero:6} -> {zero + ZERO_OFFSET:<4} "
              f"{full:8} -> {full + ZERO_OFFSET:<4} "
              f"{full - zero:6} steps = {(full - zero) / 16:.0f} deg")
    for name, _, base in UNTOUCHED:
        print(f"{name:9} ${base:02X}     (not touched -- keeps reading the truth)")

    print("\ntunable bytes (WriteRAM these to reshape without rebuilding):")
    for name, what in (("GATE", "cluster state to start in"),
                       ("T1", "arm delay, dispatches"),
                       ("T2", "timeout on the climb, dispatches"),
                       ("DOWN", "dispatches spent coming home, from the turn"),
                       ("DWELL", "dispatches held at the stop after arrival"),
                       ("DW", "that countdown"),
                       ("T3", "end dispatch; the patch rewrites this at the turn"),
                       ("T", "dispatches counted; write 0 to replay"),
                       ("PH", "0 climbing, 4 coming home"),
                       ("SNAP", "speedo needle at handover (word)")):
        print(f"  ${labels[name]:04X}  {name:6} {what}")
    print("  keep T1 < T2, and T3 > T2 as loaded (the patch recomputes it at the turn)")


if __name__ == "__main__":
    main()

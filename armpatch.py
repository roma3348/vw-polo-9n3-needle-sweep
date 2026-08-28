#!/usr/bin/env python3
"""
Emit the exact commands for the volatile RAM test of the sweep patch.

Nothing here touches EEPROM. It loads the sweep code into the patch RAM buffer and then
arms the memory patch module by hand, reproducing the loader's own register sequence
($F114-$F152). A `Reset` undoes all of it, because the module is reprogrammed from EEPROM
at every power-up. Note that the ignition key does NOT reset this cluster — it sits on
permanent power, so use `./session.sh run Reset`.

WHY THE ORDER MATTERS
The module is a sequencer, not a set of independent registers. The slot-select mask goes in
FIRST ($1E69/$1E68), and only then the address ($1E66/$1E65/$1E64) and the replacement byte
($1E67) land in the slot that mask selected. Writing the registers in ascending address
order — which is what any block write would do — would load the wrong slots. Hence
WriteRamPairs, which preserves the order given.

The hook goes live the instant the final mask is written, so the code must already be in
place. Code first, arm second, always.

Every address below is derived from `sweep.py`, so this file cannot drift out of step with
the code it is arming.

Usage: python3 armpatch.py [--arm N] [--up N] [--down N] [entry_addr] [first_slot]
"""
import sys

from sweep import ARM, UP, DOWN, SWEEP, ZERO_OFFSET, build

CODE_FILE = "sweep_code.bin"
CODE_ADDR = 0x0ECC          # $0EBE + the 14 bytes of the factory patch
HOOK_CPU = 0x6584           # JMP ($635F,X), bank0 -> physical $006584
FIRST_SLOT = 3              # slots 0-2 belong to the factory patch

# Patch module registers
R_ADDR_LO, R_ADDR_MID, R_ADDR_HI = 0x1E64, 0x1E65, 0x1E66
R_DATA, R_PER0, R_PER1 = 0x1E67, 0x1E68, 0x1E69

STATE = ("GATE", "T1", "T2", "DOWN", "DWELL", "DW", "T3", "T", "PH")


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
    entry = int(args[0], 0) if args else CODE_ADDR
    first = int(args[1], 0) if len(args) > 1 else FIRST_SLOT

    code, labels, (t1, t2, t3) = build(arm, up, down, entry)
    jmp = [0x4C, entry & 0xFF, (entry >> 8) & 0xFF]
    total_slots = first + len(jmp)

    pairs = []
    for i, byte in enumerate(jmp):
        slot = first + i
        mask = 2 << slot
        target = HOOK_CPU + i               # bank0: physical == CPU address
        pairs += [
            (R_PER1, (mask >> 8) & 0xFF),
            (R_PER0, mask & 0xFF),
            (R_ADDR_HI, (target >> 16) & 0xFF),
            (R_ADDR_MID, (target >> 8) & 0xFF),
            (R_ADDR_LO, target & 0xFF),
            (R_DATA, byte),
        ]
    final = (1 << (total_slots + 1)) - 1
    pairs += [(R_PER1, (final >> 8) & 0xFF), (R_PER0, final & 0xFF)]

    print(f"Sweep: {len(code)} bytes at ${entry:04X}. Arms {t1} dispatches after the")
    print(f"gauges report ready, climbs until the speedometer arrives (timeout {t2}),")
    print(f"then {down} dispatches coming home.\n")

    print("STEP 1 — load the code (volatile, 16 bytes per round trip):\n")
    print(f"  ./session.sh run WriteRamBlock 0x{entry:04X} {CODE_FILE}\n")
    print("STEP 2 — read it back and confirm before arming anything:\n")
    print(f"  ./session.sh run DumpMem 0x{entry:04X} 0x{len(code):02X} readback.bin")
    print(f"  cmp readback.bin {CODE_FILE} && echo IDENTICAL\n")
    print(f"STEP 3 — arm slots {first}..{total_slots - 1}. "
          f"The hook goes live on the last pair:\n")
    args_str = " ".join(f"0x{a:04X} 0x{v:02X}" for a, v in pairs)
    print(f"  ./session.sh run WriteRamPairs {args_str}\n")

    print("STEP 4 — watch. The tachometer and speedometer sweep to full scale and back,")
    print("  once. Coolant and fuel must not move at all.\n")
    print(f"  ./session.sh run DumpMem 0x{labels['GATE']:04X} "
          f"0x{len(STATE):02X} state.bin     # {', '.join(STATE)}")
    print("  ./session.sh run DumpMem 0x00DF 0x0B tach.bin        # the whole struct")
    print("  ./session.sh run DumpMem 0x00EA 0x0B speedo.bin\n")

    print("  T is the dispatch counter. PH is 0 while climbing, 4 coming home. T3 is")
    print("  rewritten at the turn, so T3 - DOWN is the dispatch the needles turned on —")
    print("  that number is the whole measurement this test exists to take. It tells us")
    print("  how fast the servo really is, and therefore whether T2 can come down.\n")

    print("STEP 5 — run it again without a reset:\n")
    print(f"  ./session.sh run WriteRAM 0x{labels['T']:04X} 0x00\n")
    print("STEP 6 — disarm without a reset, if you want the cluster back as it was:\n")
    print("  ./session.sh run WriteRamPairs 0x1E69 0x00 0x1E68 0x0F\n")

    print("If the cluster hangs or misbehaves: ./session.sh run Reset. Everything above is")
    print("volatile and the module is reloaded from EEPROM on every power-up.\n")

    print("Tunables, live-patchable while the sweep is armed:")
    for name in STATE:
        print(f"  ${labels[name]:04X}  {name}")
    print("\nEndpoints compiled in (motor steps = needle angle + 21):")
    for name, _idx, base, zero, full in SWEEP:
        print(f"  {name:8} struct ${base:02X}   rest {zero + ZERO_OFFSET:5}   "
              f"full scale {full + ZERO_OFFSET:5}")
    print(f"\n({len(pairs)} register writes in one session; "
          f"replacing {len(jmp)} bytes at CPU ${HOOK_CPU:04X}, final mask ${final:04X})")


if __name__ == "__main__":
    main()

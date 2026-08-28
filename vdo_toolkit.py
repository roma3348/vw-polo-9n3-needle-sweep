#!/usr/bin/env python3
"""
VDO Polo 9N3 Full-FIS cluster (6Q0920843 / VQMJ07HH 08.40) analysis toolkit.

Purpose: reverse-engineering helper for developing coding/feature ("Full FIS")
EEPROM changes. Everything here is READ-ONLY analysis of dumps you already own.

Memory model (verified against the reset vector and $1F0A bank-select stores):
  $0000-$1FFF : RAM + memory-mapped I/O (I/O around $1F00; timers/UART $0D..; flags $08..$09..)
  $2000-$7FFF : fixed ROM   -> bank0 file (offset = addr-0x2000)
  $8000-$FFFF : paged ROM   -> banks 1..4, selected by writing $1F0A
                bank1 is the boot bank (holds the CPU vectors at $FFFA/$FFFC/$FFFE)
                banks 2,3,4 are overlay/data banks (their $FFFx vector area is blank)

CPU core: 65C02 (uses TSB/TRB/STZ/BRA/BIT#imm -> confirmed by coherent disassembly).

Requires: pip install py65
"""
import sys, os

import glob
def _find(pat, alt="/mnt/user-data/uploads"):
    for d in (".", alt):
        hits = sorted(glob.glob(f"{d}/{pat}"))
        if hits:
            return hits[0]
    raise FileNotFoundError(pat)
BANKS = {k: _find(f"*bank{k}*.bin") for k in range(5)}

FIXED_BASE = 0x2000     # bank0
BANK_BASE  = 0x8000     # banks 1..4

raw = {k: open(v, "rb").read() for k, v in BANKS.items()}

def base_of(bank):  return FIXED_BASE if bank == 0 else BANK_BASE
def addr_of(bank, off): return base_of(bank) + off
def off_of(bank, addr): return addr - base_of(bank)

# --- absolute-addressing opcode table for cross-referencing ---------------
ABS = {
 0x0C:("TSB","abs"),0x0D:("ORA","abs"),0x0E:("ASL","abs"),
 0x1C:("TRB","abs"),0x1D:("ORA","abx"),0x1E:("ASL","abx"),0x19:("ORA","aby"),
 0x20:("JSR","abs"),0x2C:("BIT","abs"),0x2D:("AND","abs"),0x2E:("ROL","abs"),
 0x39:("AND","aby"),0x3C:("BIT","abx"),0x3D:("AND","abx"),0x3E:("ROL","abx"),
 0x4C:("JMP","abs"),0x4D:("EOR","abs"),0x4E:("LSR","abs"),
 0x59:("EOR","aby"),0x5D:("EOR","abx"),0x5E:("LSR","abx"),
 0x6C:("JMP","ind"),0x6D:("ADC","abs"),0x6E:("ROR","abs"),
 0x79:("ADC","aby"),0x7C:("JMP","abx"),0x7D:("ADC","abx"),0x7E:("ROR","abx"),
 0x8C:("STY","abs"),0x8D:("STA","abs"),0x8E:("STX","abs"),
 0x99:("STA","aby"),0x9C:("STZ","abs"),0x9D:("STA","abx"),0x9E:("STZ","abx"),
 0xAC:("LDY","abs"),0xAD:("LDA","abs"),0xAE:("LDX","abs"),
 0xB9:("LDA","aby"),0xBC:("LDY","abx"),0xBD:("LDA","abx"),0xBE:("LDX","aby"),
 0xCC:("CPY","abs"),0xCD:("CMP","abs"),0xCE:("DEC","abs"),
 0xD9:("CMP","aby"),0xDD:("CMP","abx"),0xDE:("DEC","abx"),
 0xEC:("CPX","abs"),0xED:("SBC","abs"),0xEE:("INC","abs"),
 0xF9:("SBC","aby"),0xFD:("SBC","abx"),0xFE:("INC","abx"),
}

def xref(lo, hi=None):
    """All absolute references into [lo,hi]. Returns (bank, off, cpu_addr, mnem, mode, target)."""
    if hi is None: hi = lo
    hits = []
    for b, data in raw.items():
        for i in range(len(data) - 2):
            op = data[i]
            info = ABS.get(op)
            if not info: continue
            tgt = data[i+1] | (data[i+2] << 8)
            if lo <= tgt <= hi:
                hits.append((b, i, addr_of(b, i), info[0], info[1], tgt))
    return sorted(hits, key=lambda x: (x[5], x[0], x[1]))

def find_bytes(pat):
    out = []
    for b, data in raw.items():
        i = data.find(pat)
        while i >= 0:
            out.append((b, i, addr_of(b, i)))
            i = data.find(pat, i+1)
    return out

# --- disassembler ---------------------------------------------------------
def disasm(bank, start, end):
    from py65.disassembler import Disassembler
    from py65.devices.mpu65c02 import MPU
    mpu = MPU(); d = Disassembler(mpu)
    base = base_of(bank); data = raw[bank]
    for i, byte in enumerate(data):
        mpu.memory[base + i] = byte
    pc = start; out = []
    while pc < end:
        length, text = d.instruction_at(pc)
        b = " ".join("%02X" % mpu.memory[pc+i] for i in range(length))
        out.append("$%04X  %-9s %s" % (pc, b, text))
        pc += length
    return "\n".join(out)

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "xref":
        lo = int(sys.argv[2], 16); hi = int(sys.argv[3], 16) if len(sys.argv) > 3 else lo
        for b, off, a, m, md, t in xref(lo, hi):
            print("$%04X  bank%d off 0x%04X  %s %s $%04X" % (a, b, off, m, md, t))
    elif len(sys.argv) >= 4 and sys.argv[1] == "dis":
        print(disasm(int(sys.argv[2]), int(sys.argv[3],16), int(sys.argv[4],16)))
    elif len(sys.argv) >= 3 and sys.argv[1] == "find":
        pat = bytes.fromhex(sys.argv[2])
        for b, off, a in find_bytes(pat):
            print("bank%d off 0x%04X -> $%04X" % (b, off, a))
    else:
        print(__doc__)
        print("Usage:")
        print("  python3 vdo_toolkit.py xref  <lo> [hi]      # find refs to RAM/IO addr(s)")
        print("  python3 vdo_toolkit.py dis   <bank> <s> <e> # disassemble range")
        print("  python3 vdo_toolkit.py find  <hexbytes>     # locate a byte pattern")

#!/usr/bin/env python3
"""
Disassemble a raw 65C02 blob at a given load address.

vdo_toolkit.py only knows how to reach into the ROM bank files. The reference sweep
patches are standalone blobs that get copied to a RAM address, so they need their own
entry point. Also parses the VDO patch-block header in front of the code, since that is
how the reference .bin files are stored.

Usage: python3 blobdis.py <file> [load_addr] [--raw]
       --raw treats the whole file as code (no patch header).
"""
import sys
from py65.disassembler import Disassembler
from py65.devices.mpu65c02 import MPU
from py65.memory import ObservableMemory


def disassemble(blob, load_addr, length=None):
    mpu = MPU()
    mem = ObservableMemory()
    for i, b in enumerate(blob):
        mem[(load_addr + i) & 0xFFFF] = b
    mpu.memory = mem
    dis = Disassembler(mpu)
    end = load_addr + (length if length is not None else len(blob))
    pc = load_addr
    while pc < end:
        n, text = dis.instruction_at(pc)
        raw = " ".join(f"{blob[pc - load_addr + k]:02X}" for k in range(n))
        print(f"${pc:04X}  {raw:<9} {text}")
        pc += n


def main():
    path = sys.argv[1]
    blob = open(path, "rb").read()
    raw = "--raw" in sys.argv
    args = [a for a in sys.argv[2:] if not a.startswith("--")]

    if raw:
        load = int(args[0], 0) if args else 0x0000
        disassemble(blob, load)
        return

    h0, h1, h2, h3, h4, h5 = blob[:6]
    data_len = h1 & 0x7F
    code_len = h2
    total = code_len + data_len + (h3 << 8)
    code = blob[6:6 + code_len]
    data = blob[6 + code_len:6 + total]

    print(f"header    : {' '.join(f'{b:02X}' for b in blob[:6])}")
    print(f"  code len {code_len}, data len {data_len}, total {total}")
    print(f"  H4/H5    ${h4:02X} ${h5:02X}")

    entries = []
    n = (len(data) - 2) // 4
    for i in range(n):
        hi, mid, lo, val = data[4 * i:4 * i + 4]
        entries.append(((hi << 16) | (mid << 8) | lo, val))
    print(f"entries   : {n}   mask ${data[-2]:02X}{data[-1]:02X}")
    for a, v in entries:
        print(f"  ${a:06X} <- ${v:02X}")

    # The replacement bytes spell out "JMP <load address>", so recover the base from them.
    load = int(args[0], 0) if args else None
    if load is None and n >= 3 and entries[0][1] == 0x4C:
        load = entries[1][1] | (entries[2][1] << 8)
        print(f"\nload address recovered from the JMP entries: ${load:04X}")
    if load is None:
        load = 0x0000

    print(f"\n--- code at ${load:04X} ---")
    disassemble(code, load)
    print(f"\n(code ends at ${load + code_len - 1:04X}; the patch's own variables usually "
          f"live in the last few bytes)")


if __name__ == "__main__":
    main()

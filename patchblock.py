#!/usr/bin/env python3
"""
VDO patch-block parser and builder for VQMJ07HH-08.40.

The format below is not taken from documentation — it was read straight out of this ROM's
own patch loader (bank2 $EFAE-$F159, disassembled 2026-08-27) and then verified by
reproducing, byte for byte, the patch block that is already installed in this cluster.

WHY THIS MATTERS
The cluster's EEPROM already contains a working patch (see PATCH_ENGINEERING.md §10.3). It
hooks physical $016C77 and is presumably a factory fix the cluster relies on. Our needle
sweep therefore cannot be dropped on top of it: it has to be added as extra entries in the
same block, with the code section grown and the checksum and enable mask recomputed. Doing
that by hand is how clusters get bricked, so it is done here instead.

BLOCK FORMAT (EEPROM byte $552, which the loader reaches as word index $2A9)

  header, 6 bytes:
    H0  checksum (see below)
    H1  bit7 = "a patch is present"; bits0-6 = length of the DATA section
    H2  length of the CODE section
    H3  high byte of (code_len + data_len)
    H4  must equal ROM byte $21BB, else the loader rejects the block
    H5  version check. bit7 clear -> must equal ROM $21BC.
        bit7 set -> must equal 0x80 | (ROM $21BC + sum of ROM $21B3..$21BA)
  code, H2 bytes:  65C02, copied to RAM at $0EBE and executed there
  data, H1&0x7F bytes:  N entries of 4 bytes, then a 2-byte trailer
    entry:   addr_hi, addr_mid, addr_lo, replacement_byte   (address is BIG-endian,
             and is a PHYSICAL ROM address: bank*0x8000 + (cpu_addr - 0x8000), or the
             cpu address itself in bank0)
    trailer: PER1, PER0 — the final 16-bit enable mask, PER0 first in memory order... no:
             the loader reads PER1 then PER0, so the bytes are (PER1, PER0).

  Constraints the loader enforces, all of which are checked here:
    * H0 == H1 means "no patch installed" — the block must never end up in that state
    * data_len must be even and must have bit1 set, i.e. data_len == N*4 + 2
    * N != 0
    * code_len + data_len must be even
    * code_len + data_len must fit in the RAM buffer $0EBE..$14F4 (1590 bytes)
    * PER0 bit0 must be set, or the loader flags an error after programming

  Enable mask: the loader writes the mask before loading each slot, starting at 0x0002 and
  shifting left once per entry. So bit0 is a global enable and bits 1..N enable the slots —
  PER0/PER1 = 2^(N+1) - 1. The mask is 16 bits, so the hardware ceiling is 15 substitutions.

  CHECKSUM
    s = (sum(code + data) + H5 + H4 + H3 + H2 + (H1 & 0x7F)) & 0xFF
    H0 = (s ^ 0xFF) if (s & 0x7F) == (H1 & 0x7F) else s
  The conditional exists purely so H0 can never accidentally equal H1.

Usage:
  python3 patchblock.py parse <eeprom.bin> [offset]
  python3 patchblock.py verify <eeprom.bin>          round-trip: parse, rebuild, compare
  python3 patchblock.py addhook <eeprom.bin> <out.bin> <cpu_addr> <bank> <code.bin>
      Append `code.bin` after the existing code and add three entries replacing the three
      bytes at <cpu_addr> with a JMP to the appended code. Prints the block; writes it out.
"""
import sys

BASE = 0x552          # EEPROM byte address of the block
BUF_START = 0x0EBE    # RAM buffer the code is copied to
BUF_END = 0x14F4
MAX_SLOTS = 15


def phys(cpu_addr, bank):
    """CPU address + bank number -> the physical ROM address the patch module wants."""
    if bank == 0:
        if not 0x2000 <= cpu_addr <= 0x7FFF:
            raise ValueError(f"bank0 covers $2000-$7FFF, got ${cpu_addr:04X}")
        return cpu_addr
    if not 0x8000 <= cpu_addr <= 0xFFFF:
        raise ValueError(f"banks 1-4 cover $8000-$FFFF, got ${cpu_addr:04X}")
    return bank * 0x8000 + (cpu_addr - 0x8000)


def total_length(h1, h2, h3):
    """Reproduce the loader's own length arithmetic at $F00C-$F019, exactly.

    It does an 8-BIT add of (H1 & 0x7F) + H2 into $10 and lets that addition's carry
    increment $11, which was preloaded with H3. So H3 is NOT simply the high byte of the
    total — the carry already supplies it, and H3 is whatever remains on top. Getting this
    wrong writes a header that makes the loader copy the wrong number of bytes; encoding a
    274-byte block as H3=1 asks it for 530.
    """
    lo = ((h1 & 0x7F) + h2) & 0xFF
    carry = 1 if ((h1 & 0x7F) + h2) > 0xFF else 0
    return lo + (((h3 + carry) & 0xFF) << 8)


def header_h3(code_len, data_len):
    """The H3 a given block needs. Always 0 in practice — see below."""
    total = code_len + data_len
    carry = 1 if (data_len + code_len) > 0xFF else 0
    h3 = (total >> 8) - carry
    if h3 != 0:
        raise ValueError(f"H3 would have to be {h3}; the header cannot encode this length")
    return h3


def checksum(h1, h2, h3, h4, h5, body):
    s = (sum(body) + h5 + h4 + h3 + h2 + (h1 & 0x7F)) & 0xFF
    return (s ^ 0xFF) if (s & 0x7F) == (h1 & 0x7F) else s


class Block:
    def __init__(self, header, code, entries, per):
        self.h0, self.h1, self.h2, self.h3, self.h4, self.h5 = header
        self.code = bytes(code)
        self.entries = list(entries)         # [(phys_addr, value), ...]
        self.per = per                       # 16-bit enable mask

    @classmethod
    def parse(cls, eeprom, off=BASE):
        h = list(eeprom[off:off + 6])
        h0, h1, h2, h3, h4, h5 = h
        if h0 == h1:
            raise ValueError("H0 == H1: the loader reads this as 'no patch installed'")
        data_len = h1 & 0x7F
        code_len = h2
        total = total_length(h1, h2, h3)
        body = eeprom[off + 6: off + 6 + total]
        code = body[:code_len]
        data = body[code_len:]
        n = (len(data) - 2) // 4
        entries = []
        for i in range(n):
            hi, mid, lo, val = data[4 * i: 4 * i + 4]
            entries.append(((hi << 16) | (mid << 8) | lo, val))
        per1, per0 = data[-2], data[-1]
        return cls(h, code, entries, (per1 << 8) | per0)

    def data_bytes(self):
        out = bytearray()
        for addr, val in self.entries:
            out += bytes([(addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF, val])
        out += bytes([(self.per >> 8) & 0xFF, self.per & 0xFF])
        return bytes(out)

    def serialize(self):
        """Rebuild the full block, recomputing every derived field."""
        data = self.data_bytes()
        n = len(self.entries)
        code_len, data_len = len(self.code), len(data)
        total = code_len + data_len

        problems = []
        if n == 0:
            problems.append("no entries — the loader rejects N == 0")
        if n > MAX_SLOTS:
            problems.append(f"{n} entries exceeds the 15-slot mask")
        if data_len & 1 or not (data_len & 2):
            problems.append(f"data length {data_len} is not N*4+2")
        if total & 1:
            problems.append(f"total {total} is odd; the loader requires an even length")
        if total > BUF_END - BUF_START:
            problems.append(f"total {total} exceeds the {BUF_END - BUF_START}-byte buffer")
        if data_len > 0x7F:
            problems.append(f"data length {data_len} does not fit in H1 bits 0-6")
        if code_len > 0xFF:
            problems.append(f"code length {code_len} does not fit in H2")
        # H1 holds at most 127 and H2 at most 255, so a block can never exceed 382 bytes —
        # the real ceiling is the header, not the 1590-byte RAM buffer.
        if total > 0x7F + 0xFF:
            problems.append(f"total {total} exceeds the 382 bytes the header can encode")
        if not self.per & 1:
            problems.append("PER0 bit0 clear — the loader flags an error after programming")
        if problems:
            raise ValueError("; ".join(problems))

        h1 = 0x80 | data_len
        h2 = code_len
        h3 = header_h3(code_len, data_len)
        if total_length(h1, h2, h3) != total:
            raise ValueError("header would not decode back to the intended length")
        h0 = checksum(h1, h2, h3, self.h4, self.h5, self.code + data)
        if h0 == h1:
            raise ValueError("rebuilt H0 == H1, which reads as 'no patch'")
        return bytes([h0, h1, h2, h3, self.h4, self.h5]) + self.code + data

    def show(self):
        blob = self.serialize()
        data_len, code_len = blob[1] & 0x7F, blob[2]
        print(f"header      : {' '.join(f'{b:02X}' for b in blob[:6])}")
        print(f"  H0 cksum  : ${blob[0]:02X}")
        print(f"  H1        : ${blob[1]:02X}  present=1, data length {data_len}")
        print(f"  H2        : ${blob[2]:02X}  code length {code_len}")
        print(f"  H3        : ${blob[3]:02X}  total {code_len + data_len}")
        print(f"  H4/H5     : ${blob[4]:02X} ${blob[5]:02X}  (ROM version gate)")
        print(f"code @ ${BUF_START:04X} : {' '.join(f'{b:02X}' for b in self.code)}")
        print(f"entries     : {len(self.entries)}  (mask ${self.per:04X})")
        for addr, val in self.entries:
            print(f"  ${addr:06X} <- ${val:02X}")
        print(f"total block : {len(blob)} bytes, EEPROM ${BASE:03X}-${BASE + len(blob) - 1:03X}")
        return blob


def cmd_parse(path, off):
    b = Block.parse(open(path, "rb").read(), off)
    b.show()


def cmd_verify(path):
    e = open(path, "rb").read()
    b = Block.parse(e)
    rebuilt = b.serialize()
    original = e[BASE:BASE + len(rebuilt)]
    print("original :", " ".join(f"{x:02X}" for x in original))
    print("rebuilt  :", " ".join(f"{x:02X}" for x in rebuilt))
    ok = rebuilt == original
    print("ROUND TRIP", "MATCHES — the format is understood correctly" if ok else "DIFFERS")
    return 0 if ok else 1


def cmd_addhook(path, out, cpu_addr, bank, codepath):
    e = open(path, "rb").read()
    b = Block.parse(e)
    new_code = open(codepath, "rb").read()

    entry_point = BUF_START + len(b.code)
    if entry_point > 0xFFFF:
        raise ValueError("entry point out of range")

    b.code = b.code + new_code
    target = phys(cpu_addr, bank)
    jmp = bytes([0x4C, entry_point & 0xFF, (entry_point >> 8) & 0xFF])
    for i, byte in enumerate(jmp):
        b.entries.append((target + i, byte))
    b.per = (1 << (len(b.entries) + 1)) - 1

    print(f"appending {len(new_code)} bytes of code; entry point ${entry_point:04X}")
    print(f"hooking CPU ${cpu_addr:04X} in bank {bank} = physical ${target:06X}")
    print(f"  replaced with {' '.join(f'{x:02X}' for x in jmp)} = JMP ${entry_point:04X}\n")
    blob = b.show()
    open(out, "wb").write(blob)
    print(f"\nwritten to {out} — install with: LoadEeprom 0x{BASE:X} {out}")
    print("Do NOT install until the code has been proven in RAM first.")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    if cmd == "parse":
        cmd_parse(sys.argv[2], int(sys.argv[3], 0) if len(sys.argv) > 3 else BASE)
    elif cmd == "verify":
        return cmd_verify(sys.argv[2])
    elif cmd == "addhook" and len(sys.argv) >= 7:
        cmd_addhook(sys.argv[2], sys.argv[3], int(sys.argv[4], 0),
                    int(sys.argv[5], 0), sys.argv[6])
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())

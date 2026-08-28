#!/usr/bin/env python3
"""
VDO 9N3 Full-FIS cluster (6Q0920843 / VQMJ07HH 08.40) — EEPROM obfuscation codec.

RECOVERED FROM ROM (bank2 routine $CDC0, called on every EEPROM word access via
the manager at $CAB0-$CCC1; low-level 3-wire driver on port $1FAC at $FC43/$FC7A).

The external 93C86 stores 1024 little-endian 16-bit words. Each word is obfuscated
with an address-dependent XOR key:

    default:   key_lo = (index      & 0xFF) ^ 0xC5
               key_hi = ((index>>8) & 0x03) ^ 0xC5
    window:    for page-0 indices 0x9C..0xA4 the key comes from a ROM table:
               key_lo = ROM[$538F + idx]     ^ 0x53
               key_hi = ROM[$538F + idx + 1] ^ 0x53
    raw:       index 0x116 is stored without transform (firmware special-case)

  raw_lo = value_lo ^ key_lo     (byte at file offset 2*index)
  raw_hi = value_hi ^ key_hi     (byte at file offset 2*index + 1)

Parameters are stored redundantly in consecutive slots (the store is a wear-leveled
ring log), which is how the codec was verified: neighbouring raw words differ, yet
decode to identical values.

IMPORTANT: this codec only gives you the on-chip byte<->value mapping. The store is
managed by firmware (ring pointer + integrity). Do NOT blindly overwrite live
parameter slots. Use this to (a) read the plaintext layout and (b) compute the raw
bytes for a controlled WriteEeprom into KNOWN-SAFE / spare regions.
"""
import sys, os

def _load_rom_bank0():
    import glob
    for d in (".", "/mnt/user-data/uploads"):
        hits = sorted(glob.glob(f"{d}/*bank0*.bin"))
        if hits:
            return open(hits[0], "rb").read()  # maps to $2000..$7FFF
    raise FileNotFoundError("bank0 ROM (*bank0*.bin) not found in cwd")

_BANK0 = None
def _rom(addr):
    global _BANK0
    if _BANK0 is None:
        _BANK0 = _load_rom_bank0()
    return _BANK0[addr - 0x2000]

TBL = 0x538F
CONST = 0xC5
WIN_XOR = 0x53

def key_word(index):
    """Return (key_hi, key_lo) for a given 16-bit word index (0..1023)."""
    aL = index & 0xFF
    aH = (index >> 8) & 0x03
    if aH == 0 and 0x9C <= aL <= 0xA4:
        return (_rom(TBL + aL + 1) ^ WIN_XOR, _rom(TBL + aL) ^ WIN_XOR)
    return ((aH ^ CONST), (aL ^ CONST))

def decode(raw: bytes) -> bytes:
    out = bytearray(len(raw))
    for i in range(len(raw) // 2):
        khi, klo = key_word(i)
        out[2*i]   = raw[2*i]   ^ klo
        out[2*i+1] = raw[2*i+1] ^ khi
    return bytes(out)

def encode(plain: bytes) -> bytes:
    return decode(plain)  # XOR is its own inverse

def encode_word(index: int, value: int):
    """Return (raw_lo, raw_hi) to store `value` (0..0xFFFF) at word `index`."""
    khi, klo = key_word(index)
    return ((value & 0xFF) ^ klo, ((value >> 8) & 0xFF) ^ khi)

def raw_bytes_for(byte_addr: int, value_byte: int):
    """For a single EEPROM byte address, return the raw byte to WriteEeprom.
    (byte_addr is the physical address 0..2047; keying is per-word.)"""
    index = byte_addr // 2
    khi, klo = key_word(index)
    k = klo if (byte_addr & 1) == 0 else khi
    return value_byte ^ k

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "decode":
        raw = open(sys.argv[2], "rb").read()
        outp = sys.argv[3] if len(sys.argv) > 3 else "EEPROM_decoded.bin"
        open(outp, "wb").write(decode(raw))
        print(f"decoded {len(raw)} bytes -> {outp}")
    elif len(sys.argv) >= 3 and sys.argv[1] == "encode":
        plain = open(sys.argv[2], "rb").read()
        outp = sys.argv[3] if len(sys.argv) > 3 else "EEPROM_raw.bin"
        open(outp, "wb").write(encode(plain))
        print(f"encoded {len(plain)} bytes -> {outp}")
    elif len(sys.argv) >= 4 and sys.argv[1] == "wordraw":
        idx = int(sys.argv[2], 0); val = int(sys.argv[3], 0)
        lo, hi = encode_word(idx, val)
        print(f"word {idx:#06x} value {val:#06x} -> raw bytes @off {2*idx:#06x}: {lo:02X} {hi:02X}")
    else:
        print(__doc__)
        print("Usage:")
        print("  python3 vdo_eeprom_codec.py decode  <raw.bin> [out.bin]")
        print("  python3 vdo_eeprom_codec.py encode  <plain.bin> [out.bin]")
        print("  python3 vdo_eeprom_codec.py wordraw <index> <value>")

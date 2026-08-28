#!/usr/bin/env python3
"""
Diff two DumpMem snapshots taken at the same START address, to find RAM cells that
track a physical input (e.g. RPM while the driver revs the engine).

Usage:
  python3 ramdiff.py <base_hex> <snapA.bin> <snapB.bin> [snapC.bin ...]

Example workflow (cluster address 17):
  kw1281test COMx 10400 17 DumpMem 0x0000 0x1000 ram_idle.bin
  # (driver holds ~3000 rpm)
  kw1281test COMx 10400 17 DumpMem 0x0000 0x1000 ram_3000.bin
  python3 ramdiff.py 0x0000 ram_idle.bin ram_3000.bin

Reports:
  - changed bytes (addr: A -> B)
  - 16-bit LE words that changed monotonically across the snapshots (candidate
    analogue quantities like RPM / speed / temp / fuel; a monotonic riser as RPM
    rises is a strong tach-chain candidate)
"""
import sys

def load(p): return open(p, "rb").read()

def main():
    if len(sys.argv) < 4:
        print(__doc__); return
    base = int(sys.argv[1], 0)
    snaps = [load(p) for p in sys.argv[2:]]
    n = min(len(s) for s in snaps)
    labels = [p for p in sys.argv[2:]]

    print(f"# base ${base:04X}, {len(snaps)} snapshots, {n} bytes each")
    print(f"# snapshots: {', '.join(labels)}")

    # changed bytes
    print("\n## Changed bytes (addr: values across snapshots)")
    changed = []
    for i in range(n):
        vals = [s[i] for s in snaps]
        if len(set(vals)) > 1:
            changed.append(i)
            print(f"  ${base+i:04X}: " + " -> ".join(f"{v:02X}" for v in vals))
    print(f"# {len(changed)} changed bytes")

    # monotonic 16-bit LE words (both byte orders)
    def words(s, le=True):
        out = {}
        for i in range(0, n-1):
            out[i] = (s[i] | (s[i+1] << 8)) if le else ((s[i] << 8) | s[i+1])
        return out
    print("\n## 16-bit LE words changing monotonically across snapshots (RPM-like candidates)")
    ws = [words(s, True) for s in snaps]
    for i in range(0, n-1):
        seq = [ws[k][i] for k in range(len(snaps))]
        inc = all(seq[k] < seq[k+1] for k in range(len(seq)-1))
        dec = all(seq[k] > seq[k+1] for k in range(len(seq)-1))
        if inc or dec:
            arrow = "UP  " if inc else "DOWN"
            print(f"  ${base+i:04X} {arrow}: " + " -> ".join(str(v) for v in seq))

if __name__ == "__main__":
    main()

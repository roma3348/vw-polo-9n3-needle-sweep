#!/usr/bin/env python3
"""
VDO 9N3 cluster — gauge scale calibration reader & RAM needle-cell finder.

WHY THIS EXISTS
The cluster stores, in its EEPROM dataset, a piecewise-linear curve per gauge that maps
a physical quantity (rpm, km/h, degC, fuel) to a NEEDLE ANGLE. Angles are stored in units
of 1/16 degree (the forum's "Faktor 0,0625"). That means:

  * we know the exact full-scale value each needle can be driven to, and
  * given an engine rpm we can PREDICT the number the tach's needle cell must contain.

That prediction turns Phase 1 from "look for something that rises with revs" into
"grep the RAM dump for the value 413" — which is what `find` below does.

Address map source: forum.polo9n.info thread 69327 (user -creez-), VDO generation 3+4,
which covers our VQMJ07HH 08.40. Verified against our own decrypted dump: the 070/090
1's-complement block matches on all used key slots, odometer and login code are stored
redundantly and agree, and every curve below comes out monotonic and physically sensible.
NOTE: the forum's byte boundaries for the two coolant curves are off by one; the correct
starts are 0x336 and 0x342 (6 words each), as used here.

Input is the DECRYPTED dataset (2048 bytes), e.g. eeprom_decrypted.bin.

Usage:
  python3 gaugecal.py curves  <decrypted.bin>
      Print all four gauge curves, full-scale values, and a tach prediction table.

  python3 gaugecal.py predict <decrypted.bin> <gauge> <value>
      gauge = rpm | speed | temp | fuel ; value in 1/min, km/h, degC, or curve index.
      Prints the 16-bit value the needle cell should hold.

  python3 gaugecal.py find <decrypted.bin> <gauge> <value> <ramdump.bin> [base] [tol]
      Search a DumpMem snapshot for 16-bit little-endian cells holding the predicted
      value (+/- tol, default 12). base = CPU address of dump start (default 0x0000).

  python3 gaugecal.py block <decrypted.bin> <ramdump.bin> [base] [idle_rpm]
      THE PREFERRED SEARCH. Finds the whole 4-gauge register block at once by its
      signature, from ONE snapshot taken at warm idle with the car parked. No revving.
      See "Why block beats find" below.

Why `block` beats `find`
Three of the four gauges sit at values we can predict with zero driver effort:
  * coolant, warmed up  -> EXACTLY the flat-zone angle (74-116 degC all map to one angle),
  * speedometer, parked -> the zero-point angle,
  * tachometer at idle  -> the idle angle (idle holds itself; no pedal).
The reference patches show the four gauges are four consecutive 16-bit cells, so we search
for that whole pattern rather than one number. Matching four cells at once is far more
selective than matching one, and it removes the need to hold a steady rpm while a slow
DumpMem runs. Use `find` only as a fallback.
"""
import sys

FAC_ANGLE = 0.0625  # 1/16 degree per LSB

# (name, value-curve start, value factor, angle-curve start, n points, unit)
CURVES = {
    "rpm":   ("Tachometer",   0x2ae, 1.0,    0x2b6, 4, "1/min"),
    "speed": ("Speedometer",  0x28e, 0.0625, 0x29e, 8, "km/h"),
    "fuel":  ("Fuel",         None,  None,   0x2fe, 8, "(curve index)"),
    "temp":  ("Coolant temp", 0x336, 0.125,  0x34e, 6, "degC"),
}
TEMP_CURVE_B = 0x342  # second coolant curve (hysteresis partner of 0x336)


def words(d, addr, n):
    return [d[addr + 2 * i] | (d[addr + 2 * i + 1] << 8) for i in range(n)]


def load(path):
    d = open(path, "rb").read()
    if len(d) != 2048:
        print(f"warning: expected 2048 bytes, got {len(d)}", file=sys.stderr)
    return d


def get_curve(d, gauge):
    name, vaddr, vfac, aaddr, n, unit = CURVES[gauge]
    angles = words(d, aaddr, n)
    vals = words(d, vaddr, n) if vaddr is not None else list(range(n))
    return name, vals, vfac, angles, unit


def interp(vals, angles, x):
    """Piecewise-linear physical value -> raw needle angle (1/16 deg), clamped."""
    if x <= vals[0]:
        return angles[0]
    if x >= vals[-1]:
        return angles[-1]
    for i in range(len(vals) - 1):
        lo, hi = vals[i], vals[i + 1]
        if lo <= x <= hi:
            if hi == lo:
                return angles[i]
            f = (x - lo) / (hi - lo)
            return round(angles[i] + f * (angles[i + 1] - angles[i]))
    return angles[-1]


def cmd_curves(d):
    print("Needle angles are in units of 1/16 degree.\n")
    for g in ("rpm", "speed", "temp", "fuel"):
        name, vals, vfac, angles, unit = get_curve(d, g)
        print(f"=== {name} ({g}) ===")
        if vfac is not None:
            print("  value :", " ".join(f"{v * vfac:9.2f}" for v in vals), unit)
        else:
            print("  point :", " ".join(f"{v:9d}" for v in vals))
        print("  angle :", " ".join(f"{a * FAC_ANGLE:9.2f}" for a in angles), "deg")
        print("  raw   :", " ".join(f"{a:9d}" for a in angles))
        print(f"  FULL SCALE = {angles[-1]} raw ({angles[-1] * FAC_ANGLE:.2f} deg)"
              f"  ZERO = {angles[0]} raw ({angles[0] * FAC_ANGLE:.2f} deg)\n")

    b = words(d, TEMP_CURVE_B, 6)
    print(f"=== Coolant second curve @{TEMP_CURVE_B:#05x} (hysteresis partner) ===")
    print("  degC  :", " ".join(f"{v * 0.125:9.2f}" for v in b), "\n")

    name, vals, vfac, angles, unit = get_curve(d, "rpm")
    print("=== Tach prediction table (what the needle cell should read) ===")
    print("     rpm |   raw |    hex | degrees")
    for r in (0, 800, 840, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 5000, 6000, 7000):
        a = interp(vals, angles, r)
        print(f"  {r:6d} | {a:5d} | 0x{a:04X} | {a * FAC_ANGLE:7.2f}")


def cmd_predict(d, gauge, value):
    name, vals, vfac, angles, unit = get_curve(d, gauge)
    raw_in = value / vfac if vfac else value
    a = interp(vals, angles, raw_in)
    print(f"{name}: {value} {unit} -> needle cell should hold {a} (0x{a:04X}) "
          f"= {a * FAC_ANGLE:.2f} deg")
    print(f"  little-endian bytes in RAM: {a & 0xFF:02X} {(a >> 8) & 0xFF:02X}")
    return a


def cmd_find(d, gauge, value, dumppath, base, tol):
    a = cmd_predict(d, gauge, value)
    ram = open(dumppath, "rb").read()
    print(f"\nsearching {len(ram)} bytes from {base:#06x}, tolerance +/-{tol}")
    hits = []
    for i in range(len(ram) - 1):
        v = ram[i] | (ram[i + 1] << 8)
        if abs(v - a) <= tol:
            hits.append((base + i, v))
    if not hits:
        print("  no candidate cells found — widen tol, or the gauge may be damped/offset")
        return
    for addr, v in hits:
        print(f"  ${addr:04X}  = {v:5d} (0x{v:04X})   delta {v - a:+d}")
    print(f"\n{len(hits)} candidates. Repeat at a different rpm and intersect the lists:")
    print("  the real needle cell tracks the prediction at EVERY rpm.")


def cmd_block(d, dumppath, base, idle_rpm, fuel_frac=None):
    """Find the 4-gauge register block by its signature in one snapshot.

    idle_rpm > 0  -> engine idling, coolant assumed warmed into the flat zone.
    idle_rpm == 0 -> engine off with ignition on: the tach rests at its zero angle and,
                     if the temperature needle is sitting at the bottom of the dial, the
                     coolant cell is at ITS zero angle too. Weaker than the warm-idle
                     signature (three of the four values are small), so pass fuel_frac
                     (0..1, the fraction of a tank the needle shows) to pin the one cell
                     that is not near zero.
    """
    ram = open(dumppath, "rb").read()

    _, rvals, _, rang, _ = get_curve(d, "rpm")
    _, _, _, sang, _ = get_curve(d, "speed")
    _, _, _, cang, _ = get_curve(d, "temp")
    _, _, _, fang, _ = get_curve(d, "fuel")
    fmin, fmax = fang[0], fang[-1]

    speed = sang[0]                      # parked
    running = idle_rpm > 0
    tach = interp(rvals, rang, idle_rpm) if running else rang[0]
    coolant = cang[2] if running else cang[0]
    tach_tol = 90 if running else 3

    if fuel_frac is None:
        fuel_lo, fuel_hi = fmin - 4, fmax + 4
        fuel_desc = f"{fmin}..{fmax} (range check only)"
    else:
        # The fuel curve's points are evenly spaced tank fractions, so interpolate the
        # needle angle across the point index rather than a physical quantity.
        f = max(0.0, min(1.0, fuel_frac)) * (len(fang) - 1)
        i = int(f)
        frac = f - i
        target = (fang[i] if i >= len(fang) - 1
                  else round(fang[i] + frac * (fang[i + 1] - fang[i])))
        fuel_lo, fuel_hi = target - 120, target + 120
        fuel_desc = f"~{target} (+/-120, needle read by eye)"

    state = f"idle {idle_rpm:.0f} rpm" if running else "engine off, cold"
    print(f"State assumed: {state}")
    print("Looking for four consecutive 16-bit LE cells matching:")
    print(f"  coolant          = {coolant:5d}  +/-2   <- {'warm flat zone' if running else 'needle at bottom of dial'}")
    print(f"  speedo (parked)  = {speed:5d}  +/-3   <- exact")
    print(f"  tach             = {tach:5d}  +/-{tach_tol} <- {'idle varies' if running else 'exact, at rest'}")
    print(f"  fuel             = {fuel_desc}\n")

    # each expected gauge: (label, predicate)
    want = [
        ("coolant", lambda v: abs(v - coolant) <= 2),
        ("speedo",  lambda v: abs(v - speed) <= 3),
        ("tach",    lambda v: abs(v - tach) <= tach_tol),
        ("fuel",    lambda v: fuel_lo <= v <= fuel_hi),
    ]

    def best_assignment(vals):
        """Max one-to-one matches between the 4 cells and the 4 expected gauges."""
        best, order = 0, None
        for perm in _perms(range(4)):
            names, n = [], 0
            for slot, wi in enumerate(perm):
                label, pred = want[wi]
                if pred(vals[slot]):
                    names.append(label)
                    n += 1
                else:
                    names.append("-")
            if n > best:
                best, order = n, names
        return best, order

    hits = []
    for i in range(0, len(ram) - 8):
        vals = [ram[i + 2 * k] | (ram[i + 2 * k + 1] << 8) for k in range(4)]
        n, order = best_assignment(vals)
        if n >= 3:
            hits.append((n, base + i, vals, order))

    if not hits:
        print("No 4-gauge block matched 3 of 4. Options:")
        print("  * is the engine actually warmed up? coolant must be in the flat zone")
        print("  * widen the dump range and retry")
        print("  * fall back to `find` with two rpm points")
        return

    hits.sort(key=lambda h: (-h[0], h[1]))
    for n, addr, vals, order in hits[:20]:
        print(f"{n}/4  block at ${addr:04X}")
        for k in range(4):
            v = vals[k]
            tag = order[k] if order else "-"
            mark = "  <-- " + tag if tag != "-" else ""
            print(f"        ${addr + 2 * k:04X} = {v:5d} (0x{v:04X}){mark}")
        print(f"        flags byte would be ${addr + 8:04X}\n")

    print("Confirm the winner with two quick ReadRAM calls while blipping the throttle —")
    print("the tach cell must move up and come back. That takes seconds, not a whole dump.")


def _perms(seq):
    seq = list(seq)
    if len(seq) <= 1:
        yield seq
        return
    for i in range(len(seq)):
        for rest in _perms(seq[:i] + seq[i + 1:]):
            yield [seq[i]] + rest


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd, path = sys.argv[1], sys.argv[2]
    d = load(path)
    if cmd == "curves":
        cmd_curves(d)
    elif cmd == "predict" and len(sys.argv) >= 5:
        cmd_predict(d, sys.argv[3], float(sys.argv[4]))
    elif cmd == "block" and len(sys.argv) >= 4:
        base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x0000
        idle = float(sys.argv[5]) if len(sys.argv) > 5 else 840.0
        fuel = float(sys.argv[6]) if len(sys.argv) > 6 else None
        cmd_block(d, sys.argv[3], base, idle, fuel)
    elif cmd == "find" and len(sys.argv) >= 6:
        base = int(sys.argv[6], 0) if len(sys.argv) > 6 else 0x0000
        tol = int(sys.argv[7], 0) if len(sys.argv) > 7 else 12
        cmd_find(d, sys.argv[3], float(sys.argv[4]), sys.argv[5], base, tol)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

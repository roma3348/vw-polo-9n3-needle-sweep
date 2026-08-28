#!/usr/bin/env python3
"""
Minimal two-pass 65C02 assembler — just the subset the sweep patch needs.

Hand-assembling ~100 bytes of 6502 and getting every branch offset right is exactly the
kind of task that produces a cluster that will not boot. This exists so the sweep is
written as readable source, assembled mechanically, and then disassembled back for a
byte-level check against what was intended.

Supported operand forms:
    (none)              implied            PHY
    A                   accumulator        LSR A
    #$nn / #label       immediate          LDA #$0E
    #<label / #>label   low / high byte    LDA #<TARGET
    $nn                 zero page          LDA $86
    $nn,X / $nn,Y       zero page indexed  STA $03,X
    $nnnn               absolute           STA $0268
    label,X / label,Y   absolute indexed   LDA POS,Y
    ($nnnn,X)           indexed indirect   JMP ($635F,X)
    label               branch target      BNE EXIT
Labels are defined as `NAME:` at the start of a line, and may be used as `LABEL+n`.
Directives: `.byte`, `.word`.

A bare `$nn` (two hex digits) is assembled zero page; a label is assembled absolute,
because every label in the patch lives at $0Exx-$0Fxx. That distinction matters for the
struct writes: `STA $03,X` with X = $F5 must be zero-page indexed, which wraps inside
page zero and is a byte shorter than the absolute form.
"""

IMPLIED = {
    "PHY": 0x5A, "PLY": 0x7A, "PHA": 0x48, "PLA": 0x68,
    "PHX": 0xDA, "PLX": 0xFA,
    "CLC": 0x18, "SEC": 0x38, "NOP": 0xEA, "RTS": 0x60,
    "TAX": 0xAA, "TXA": 0x8A, "INX": 0xE8, "DEX": 0xCA,
    "TAY": 0xA8, "TYA": 0x98, "INY": 0xC8, "DEY": 0x88,
}
ACCUMULATOR = {"LSR": 0x4A, "ASL": 0x0A, "ROR": 0x6A, "ROL": 0x2A,
               "INC": 0x1A, "DEC": 0x3A}
BRANCHES = {
    "BNE": 0xD0, "BEQ": 0xF0, "BCC": 0x90, "BCS": 0xB0,
    "BPL": 0x10, "BMI": 0x30, "BRA": 0x80,
}
# mnemonic -> {mode: opcode}; modes: imm, zp, zpx, zpy, abs, absx, absy
ADDRESSED = {
    "LDA": {"imm": 0xA9, "zp": 0xA5, "zpx": 0xB5, "abs": 0xAD, "absx": 0xBD, "absy": 0xB9},
    "STA": {"zp": 0x85, "zpx": 0x95, "abs": 0x8D, "absx": 0x9D, "absy": 0x99},
    "CMP": {"imm": 0xC9, "zp": 0xC5, "zpx": 0xD5, "abs": 0xCD, "absx": 0xDD, "absy": 0xD9},
    "CPX": {"imm": 0xE0, "zp": 0xE4, "abs": 0xEC},
    "CPY": {"imm": 0xC0, "zp": 0xC4, "abs": 0xCC},
    "ADC": {"imm": 0x69, "zp": 0x65, "zpx": 0x75, "abs": 0x6D, "absx": 0x7D, "absy": 0x79},
    "SBC": {"imm": 0xE9, "zp": 0xE5, "zpx": 0xF5, "abs": 0xED, "absx": 0xFD, "absy": 0xF9},
    "AND": {"imm": 0x29, "zp": 0x25, "zpx": 0x35, "abs": 0x2D, "absx": 0x3D, "absy": 0x39},
    "ORA": {"imm": 0x09, "zp": 0x05, "zpx": 0x15, "abs": 0x0D, "absx": 0x1D, "absy": 0x19},
    "LDX": {"imm": 0xA2, "zp": 0xA6, "zpy": 0xB6, "abs": 0xAE, "absy": 0xBE},
    "LDY": {"imm": 0xA0, "zp": 0xA4, "zpx": 0xB4, "abs": 0xAC, "absx": 0xBC},
    "STX": {"zp": 0x86, "zpy": 0x96, "abs": 0x8E},
    "STY": {"zp": 0x84, "zpx": 0x94, "abs": 0x8C},
    "INC": {"zp": 0xE6, "zpx": 0xF6, "abs": 0xEE, "absx": 0xFE},
    "DEC": {"zp": 0xC6, "zpx": 0xD6, "abs": 0xCE, "absx": 0xDE},
    "STZ": {"zp": 0x64, "zpx": 0x74, "abs": 0x9C, "absx": 0x9E},
    "ROR": {"zp": 0x66, "zpx": 0x76, "abs": 0x6E, "absx": 0x7E},
    "ROL": {"zp": 0x26, "zpx": 0x36, "abs": 0x2E, "absx": 0x3E},
    "LSR": {"zp": 0x46, "zpx": 0x56, "abs": 0x4E, "absx": 0x5E},
    "ASL": {"zp": 0x06, "zpx": 0x16, "abs": 0x0E, "absx": 0x1E},
    "TSB": {"zp": 0x04, "abs": 0x0C},
    "TRB": {"zp": 0x14, "abs": 0x1C},
    "JSR": {"abs": 0x20},
    "JMP": {"abs": 0x4C},
}


class AsmError(Exception):
    pass


def _split(line):
    line = line.split(";")[0].rstrip()
    if not line.strip():
        return None, None, None
    label = None
    if not line[0].isspace():
        head, _, rest = line.partition(":")
        if not _:
            raise AsmError(f"label must end with ':' -> {line!r}")
        label = head.strip()
        line = rest
    parts = line.strip().split(None, 1)
    if not parts:
        return label, None, None
    return label, parts[0].upper(), (parts[1].strip() if len(parts) > 1 else None)


def _value(tok, labels, need):
    """Resolve a numeric token, optionally `SYMBOL+n` / `SYMBOL-n`."""
    tok = tok.strip()
    for op, sign in (("+", 1), ("-", -1)):
        head, sep, tail = tok.partition(op)
        if sep and head.strip():
            return _value(head, labels, need) + sign * _value(tail, labels, need)
    if tok.startswith("$"):
        return int(tok[1:], 16)
    if tok.isdigit():
        return int(tok)
    if tok in labels:
        return labels[tok]
    if need:
        raise AsmError(f"unknown symbol {tok!r}")
    return 0


def assemble(source, origin):
    labels = {}
    for final in (False, True):
        out = bytearray()
        pc = origin
        for lineno, line in enumerate(source.splitlines(), 1):
            try:
                label, mnem, operand = _split(line)
                if label:
                    if not final:
                        labels[label] = pc
                    elif labels[label] != pc:
                        raise AsmError(f"label {label} moved between passes")
                if mnem is None:
                    continue

                if mnem == ".BYTE":
                    vals = [_value(t, labels, final) & 0xFF
                            for t in operand.split(",")]
                    out += bytes(vals)
                    pc += len(vals)
                    continue
                if mnem == ".WORD":
                    for t in operand.split(","):
                        v = _value(t, labels, final) & 0xFFFF
                        out += bytes([v & 0xFF, v >> 8])
                        pc += 2
                    continue

                if mnem in IMPLIED:
                    out.append(IMPLIED[mnem])
                    pc += 1
                    continue

                if operand and operand.upper() == "A" and mnem in ACCUMULATOR:
                    out.append(ACCUMULATOR[mnem])
                    pc += 1
                    continue

                if mnem in BRANCHES:
                    target = _value(operand, labels, final)
                    delta = target - (pc + 2)
                    if final and not -128 <= delta <= 127:
                        raise AsmError(f"branch out of range: {delta}")
                    out += bytes([BRANCHES[mnem], delta & 0xFF])
                    pc += 2
                    continue

                if mnem == "JMP" and operand.startswith("(") and operand.upper().endswith(",X)"):
                    v = _value(operand[1:-3], labels, final)
                    out += bytes([0x7C, v & 0xFF, v >> 8])
                    pc += 3
                    continue

                table = ADDRESSED.get(mnem)
                if table is None:
                    raise AsmError(f"unsupported mnemonic {mnem!r}")

                if operand.startswith("#"):
                    tok = operand[1:]
                    if tok.startswith("<"):
                        v = _value(tok[1:], labels, final) & 0xFF
                    elif tok.startswith(">"):
                        v = (_value(tok[1:], labels, final) >> 8) & 0xFF
                    else:
                        v = _value(tok, labels, final) & 0xFF
                    if "imm" not in table:
                        raise AsmError(f"{mnem} has no immediate form")
                    out += bytes([table["imm"], v])
                    pc += 2
                    continue

                # Optional ",X" / ",Y" index suffix.
                base, index = operand.strip(), ""
                if base.upper().endswith(",X") or base.upper().endswith(",Y"):
                    base, index = base[:-2].strip(), base[-1].lower()

                v = _value(base, labels, final)
                # A bare $nn token is zero page; anything wider, or any label
                # (our labels all live above $0100), is absolute.
                explicit_zp = base.startswith("$") and len(base) <= 3
                mode = {"": "zp", "x": "zpx", "y": "zpy"}[index] if explicit_zp else \
                       {"": "abs", "x": "absx", "y": "absy"}[index]
                if mode not in table:
                    raise AsmError(f"{mnem} has no {mode} form")
                if mode.startswith("zp"):
                    out += bytes([table[mode], v & 0xFF])
                    pc += 2
                else:
                    out += bytes([table[mode], v & 0xFF, (v >> 8) & 0xFF])
                    pc += 3
            except AsmError as e:
                raise AsmError(f"line {lineno}: {e}\n  {line}") from None
    return bytes(out), labels

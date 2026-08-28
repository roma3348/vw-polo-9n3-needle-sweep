# Field cheat-sheet — at the car

Status: **the needle sweep is built, tested and installed.** This file is now a working
reference for talking to the cluster, not a plan.

## Getting comms up

```
./session.sh port          # detects the adapter and remembers it
./session.sh run ReadIdent # expect 6Q0920843 KOMBIINSTRUMENT VDO V06, coding 01144
```

**The port must be the FTDI serial number (e.g. `AB0CD1EF`), not `/dev/cu.usbserial-AB0CD1EF`.**
This reverses what this file said before, and it cost an evening to find out. .NET's
`SerialPort.BreakState` does not drive the K-line on macOS, so the 5-baud wakeup never
reaches the cluster through a `/dev/cu.*` node — the tool reports "controller did not wake
up" while the cable and the car are both fine. The serial-number form takes kw1281test's
FTDI D2XX path instead, which needs `libftd2xx.dylib` next to the binary; it is already
there, copied out of `D2XX1.4.35.dmg`. `session.sh port` now returns the serial number, so
just run it and don't hand-edit `.session_port`.

If a fresh copy of kw1281test exits silently with code 137:
`xattr -d com.apple.quarantine kw1281test`

## Resetting the cluster — use `Reset`, never the key

```
./session.sh run Reset
```

**Turning the ignition off and on does NOT reset this cluster.** It sits on permanent power
for the clock and odometer, so the CPU keeps running and RAM survives — including anything
we loaded. A freshly written EEPROM patch stays inert and looks exactly like a rejected
block until a real reset happens. A long KL15-off (a minute or more) does let it sleep and
reset, which is how the sweep plays in normal use, but for testing use `Reset`.

To check a patch really came up: `$6584` should read `4C CC 0E` (not `7C 5F 63`), and
`$0944` should read `06` (bit 7 there means the loader rejected something).

## Golden rules

- **WriteRAM for experiments.** It is volatile, and `Reset` or a sleep clears it.
- **EEPROM writes only when a change is proven and you have said go.** They are permanent.
- **If the hook is already armed, disarm before overwriting the code at `$0ECC`.** The block
  write takes a dozen round trips and the CPU keeps executing `$0ECC` at 42 Hz throughout.
  `WriteRamPairs 0x1E69 0x00 0x1E68 0x0F` off, `... 0x1E68 0x7F` on. The slot contents
  survive, so re-arming is that one pair — no need to reprogram the addresses.
- Parked, handbrake on, engine off or idling. Never fiddle with the cluster while driving.
- If the dash freezes or acts strange: `./session.sh run Reset`.
- Every command and its output is appended to `logs/session_YYYYMMDD.log` automatically.

## Undo, if something needs undoing

```
./session.sh run LoadEeprom 0x552 restore_block.bin    # remove the sweep entirely
./session.sh run LoadEeprom 0x252 revert_cruise.bin    # cruise lamp back to "when on"
./session.sh run LoadEeprom 0x4DE revert_welcome.bin   # greeting back off
./session.sh run Reset
```

Full backups, newest last: `ClusterBackup_preinstall.bin` (factory patch block — this is
the one `sweep_block.bin` is built from), `ee_before_coding.bin`,
`ClusterBackup_20260828.bin`, `eeprom_installed_20260828.bin`.

## Watching the sweep without looking at the needles

```
./session.sh run DumpMem 0x0F8A 0x0B state.bin
```
gives `GATE T1 T2 DOWN DWELL DW T3 T PH SNAP.lo SNAP.hi`:

| | | |
|---|---|---|
| `$0F8A` | GATE | cluster state to start in, `$0E` |
| `$0F8B` | T1 | dispatches held after the gauges report ready |
| `$0F8C` | T2 | timeout on the climb — normally the needle beats it |
| `$0F8D` | DOWN | dispatches spent coming home, counted from the turn |
| `$0F8E` | DWELL | dispatches held at the stop once the needle arrives |
| `$0F8F` | DW | that countdown |
| `$0F90` | T3 | end dispatch; **the patch rewrites this at the turn** |
| `$0F91` | T | dispatches counted; write 0 to replay without a reset |
| `$0F92` | PH | 0 climbing, 4 coming home |
| `$0F93` | SNAP | word: the speedometer needle at the instant we handed back |

**`T3 - DOWN` is the dispatch the needles turned round on, and `that - DWELL` is the
dispatch they reached the stop.** At 42 Hz those are the only two numbers worth taking.
Measured from a real reset: arrival 34 (0.81 s), turn 54 (1.29 s), end 126 (3.00 s).

`SNAP` is the diagnostic that settled the last open question: it records the speedometer's
commanded position on the dispatch the patch let go. It must read 28. If it ever reads
higher, `DOWN` is too short and the needle is still settling when we hand back.

Keep `T1 < T2`, and keep `T2 + DOWN <= 255` — the patch computes `T3 = T + DOWN` at the
turn and a byte overflow there would end the sweep instantly.

Replay without a reset: `./session.sh run WriteRAM 0x0F91 0x00`. The arming pass
resets `PH` and `DW` itself, so that one write is enough.

## The gauge struct, if a needle ever looks wrong

`./session.sh run DumpMem 0x00DF 0x0B tach.bin` (speedo `0x00EA`, coolant `0x00D4`,
fuel `0x00F5`). Bytes are `demand.lo demand.hi f1 s1.lo s1.hi f2 s2.lo s2.hi vel ? mode`.
A healthy parked gauge reads demand = stage1 = stage2 = rest, vel 0, mode `$18`
(tach rest 35, speedo 28, coolant 88, fuel 69).

If a needle is stuck at 0 against its stop with mode `$00`, it fell out of the running
state: `./session.sh run WriteRamPairs 0x00E9 0x16 0x00F4 0x16` puts the tachometer and
speedometer back through `$59DF`, which resyncs the coil and returns them to mode `$18`.
That was needed once, after forcing mode `$04` by hand to try to fake a cold start —
which does not work, because the alignment chain ends in mode `$00` and it is a different
part of the state machine that promotes a gauge from there to `$18`.

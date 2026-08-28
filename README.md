# Needle sweep for the VW Polo 9N3 VDO cluster

A power-on needle sweep (*Zeigerlauf*, "staging", "ceremony") for the VDO instrument
cluster **6Q0920843**, ROM **VQMJ07HH 08.40**. The tachometer and speedometer run to full
scale and back in **3.0 seconds** every time the cluster wakes. Coolant and fuel do not
move and keep showing the truth throughout.

It installs as a patch block in the cluster's own EEPROM, using the CDC16xxF-E's Memory
Patch Module — the same mechanism the factory uses to fix ROM bugs in the field. No
soldering, no chip removal: everything goes over the K-line with
[kw1281test](https://github.com/gmenounos/kw1281test).

```
arm 3 dispatches, climb 0.81 s, hold 0.48 s at the stop, home by 3.00 s
```

---

## ⚠ Read this before you touch anything

**This will brick your cluster if you get it wrong.** A cluster that will not boot needs
to come out of the dash and be recovered with an EEPROM programmer.

1. **The patch is locked to one exact ROM version.** The block header carries
   `H4/H5 = $40/$BD`, which the cluster's loader checks against `VQMJ07HH 08.40`. On any
   other ROM the loader refuses it. Check yours first — `kw1281test <port> 10400 17
   ReadSoftwareVersion` — and if it does not match *exactly*, stop. The patch would need
   porting: every RAM and ROM address in it is version-specific.
2. **Back up the whole EEPROM first and keep the file.**
   `kw1281test <port> 10400 17 DumpEeprom 0 2048 ClusterBackup.bin`
   That backup contains your immobiliser login code (SKC), VIN and key data — treat it
   like a key, and never publish it.
3. **Prove it in RAM before writing EEPROM.** `python3 armpatch.py` prints the exact
   volatile procedure. RAM is undone by a `Reset`; EEPROM is not.
4. **This cluster runs on permanent power.** Turning the ignition off and on does **not**
   reset it, so a freshly written patch stays inert and looks exactly like a rejected one.
   Use `kw1281test <port> 10400 17 Reset`.
5. Car parked, handbrake on, engine off or idling. Never while driving.

## What it does, and why it is fast

The naive way to sweep a needle is to draw the motion yourself: step the commanded
position a little further every time the main loop comes round. That is what the first
version of this patch did, and it is slow — the foreground loop runs at 42 Hz, the servo's
rate limiter allows 30 motor steps per update, and 30 × 42 = 79 °/s. Pushing bigger steps
does not help: a jump of 64 steps is a quarter of the 256-step electrical cycle and leaves
the rotor no preferred direction, which stalls the needle outright.

This version does not draw the motion at all. It writes the **endpoints** and lets the
cluster's own servo — which lives in an interrupt and updates several times more often —
draw them, exactly as VDO's own field patches do. Measured on the car: **294 °/s**.

Per dispatch, for the two swept gauges only:

| written | why |
|---|---|
| mirror `$0266+2g` | the cell the cluster reloads the demand from |
| demand `+0,+1` | the target in motor steps, computed the ROM's own way (`angle + 21`) |
| filter stage 1 `+3,+4` | the one field only the servo writes, so the cluster cannot undo it |
| mode `+$0A` = `$18` | keeps the servo servoing, and overrides the slow startup walk |
| `$0261`, two bits | makes `$5790` skip its demand reload for the length of the sweep |

**Filter stage 2 — the number the coil driver actually consumes — is never written.** That
is the safety argument: every step the motor takes still came out of the factory rate
limiter, so nothing this patch does can desynchronise the electrical phase or drive a
needle into its end stop.

The turn at the top is the needle's decision, not a counter: the patch waits until the
speedometer reports it has arrived, holds briefly so it creeps the last fraction of a
degree in, then comes home. How many servo updates happen per dispatch depends on the
interrupt period, which is not in any ROM image we have — so a fixed climb length would
have been a guess.

The full engineering account is in [`PATCH_ENGINEERING.md`](PATCH_ENGINEERING.md) §17,
including the tug-of-war with the cluster over the demand register and how it was settled.

## Install

```sh
python3 sweep.py                 # build sweep_code.bin
python3 emu_sweep.py             # run it against the real ROM in an emulator (see below)
python3 armpatch.py              # print the volatile RAM-test procedure, and follow it
# only once that works:
kw1281test <port> 10400 17 LoadEeprom 0x552 sweep_block.bin
kw1281test <port> 10400 17 Reset
```

`sweep_block.bin` is 286 bytes: a 262-byte patch block followed by `$FF` padding, so a
single write also clears the tail of any longer block that was there before.

### Uninstall

```sh
kw1281test <port> 10400 17 LoadEeprom 0x552 restore_block.bin
kw1281test <port> 10400 17 Reset
```

`restore_block.bin` is the factory patch block this cluster shipped with — the sweep is
appended to it and never overwrites it, so recovery is one command.

### Tuning without rebuilding

The shape lives in single bytes inside the loaded image, so it can be changed with
`WriteRAM` and no reflash. [`FIELD_CHEATSHEET.md`](FIELD_CHEATSHEET.md) has the address
table and what each byte does. `python3 sweep.py --arm N --up N --down N` rebuilds.

## What's in here

| | |
|---|---|
| `sweep.py` | builds the patch: 65C02 source, endpoints from the EEPROM gauge curves |
| `asm65.py` | small two-pass 65C02 assembler, so no branch offset is computed by hand |
| `emu_sweep.py` | bench harness — runs the patch against the **real** ROM servo, 10 checks |
| `patchblock.py` | parse / verify / extend a VDO patch block, header checksum included |
| `armpatch.py` | prints the volatile RAM-test procedure, addresses derived from `sweep.py` |
| `blobdis.py` | disassemble a patch blob at its load address |
| `gaugecal.py` | read the gauge curves out of an EEPROM dataset; predict needle cells |
| `vdo_toolkit.py`, `vdo_eeprom_codec.py`, `ramdiff.py` | dump/ROM helpers |
| `session.sh` | wrapper that logs every command and its output |
| `PATCH_ENGINEERING.md` | how the cluster works and how the patch was built. The main document |
| `EEPROM_MAP.md` | the dataset: gauge curves, coding bytes, service intervals, what is where |
| `FIELD_CHEATSHEET.md` | what to type at the car |
| `VDO_9N3_FIS_analysis.md` | the FIS / display side, including a documented negative result |
| `TEST_PLAN.md`, `CLAUDE.md` | the plan as it stood, and the working rules the project ran under |

## What is deliberately not here

- **EEPROM dumps.** They carry the immobiliser SKC, the VIN, key transponder data and the
  odometer. Those values are redacted from the documentation too — the technique for
  finding each field is described, the values are not.
- **ROM dumps** (`VQMJ07HH_bank*.bin`). That is VDO's firmware. Use your own cluster's.
  `emu_sweep.py` needs `bank0` at `$2000-$7FFF` to run, so it will not work without one.
- **`reference/`** — the VDO patch-module documentation and the reference needle-sweep
  patches this work is built on. Not redistributed; get them from
  [gmenounos/vwcluster](https://github.com/gmenounos/vwcluster).
- **kw1281test binaries and sources.** This project used a locally modified build. Three
  changes were needed and they are described in `PATCH_ENGINEERING.md` §13: a fix for
  `WriteRAM` ignoring its `VALUE` argument, plus two new commands, `WriteRamBlock` (write a
  file to RAM in 16-byte chunks — the cluster silently ignores longer block writes) and
  `WriteRamPairs` (write address/value pairs **in the order given**, which the memory patch
  module's register sequencer requires). Upstream is
  [gmenounos/kw1281test](https://github.com/gmenounos/kw1281test).

## Credits

- **Gene Menounos** — [vwcluster](https://github.com/gmenounos/vwcluster) and
  [kw1281test](https://github.com/gmenounos/kw1281test): the memory patch module
  documentation, the odometer algorithm and the reference sweep patches. This project
  would not exist without that work, and reading those patches is what turned this one
  from slow to fast.
- **Bodie Royle** — the original needle sweep patches.
- The **polo9N.info / polo6R.info** forum thread on PQ24 cluster datasets, for the coding
  byte map.

## License

MIT — see [`LICENSE`](LICENSE).

Nothing here is affiliated with, endorsed by or supported by Volkswagen AG or Continental
/ VDO. It is a retrofit on hardware the author owns. Use it on your own.

# Ryzen Curve Optimizer

A small, standalone PySide6 applet for tuning the AMD Ryzen Curve
Optimizer (`ryzenadj --set-coall` / `--set-coper`) on Zen2/Zen3/Zen4
CPUs, with a Gruvbox-themed UI.

![icon](icon.png)

---

## ⚠️ Disclaimer

**Undervolting and Curve Optimizer changes can make your system
unstable, and in rare cases can damage hardware if pushed to extreme
values. Use this tool at your own risk.**

- This applet does not validate whether a given offset is *safe* for
  your specific chip — it only validates that the value is within the
  numeric range the tool allows (`-50`..`+20`) and correctly formatted.
  Whether a given offset is stable on your CPU is something only you
  can determine, through your own testing.
- Applying an offset that is too aggressive can cause crashes, kernel
  panics, corrupted data from an interrupted write, or a system that
  fails to boot until the Curve Optimizer is reset. It is unlikely
  but not impossible for sustained instability under load to damage
  hardware over time.
- The Curve Optimizer state applied by this tool (and by `ryzenadj`
  in general) is **not persistent across reboots** — it must be
  re-applied each boot (e.g. via a startup script/service of your
  own). This also means that if a value makes your system unbootable,
  a reboot alone typically clears it back to stock behavior.
- This tool is provided as-is, with no warranty of any kind. The
  author is not responsible for any data loss, instability, or
  hardware damage resulting from its use.
- If you are not comfortable testing CPU voltage/offset values and
  recovering from a failed boot, do not use this tool.

**Test changes incrementally, one small step at a time, and always
verify stability under real load (stress test, not just idle) before
trusting an offset.**

---

## What this is

`ryzenadj`'s `--set-coper` talks directly to the CPU's SMU (System
Management Unit), which addresses cores by a **fixed 8-slot-per-CCD
layout** — every consumer/mobile Zen2/Zen3/Zen4 part addresses 8 slots
per CCD, whether or not all 8 are physically populated on a given SKU.
This is *not* necessarily the same numbering as the OS's logical core
IDs.

- On a full-core part (e.g. a 16-core/2×8 Ryzen 9 7845HX) the two
  happen to line up 1:1.
- On a part with disabled cores per CCD — e.g. a 6-core CCD used in
  some 12-core parts — only 6 of the 8 SMU slots are physically
  populated, and the OS only ever sees those 6. *Which* 6 of the 8 SMU
  slots they are cannot be discovered automatically by this or any
  other userspace tool — it depends on which physical cores AMD's
  binning process disabled on that specific chip.

So this applet always shows the full, fixed 8-slot-per-CCD grid (the
real SMU addressing space) and lets you manually mark, via the
**Disable** checkbox, any slot that doesn't correspond to a real core
on your chip. When a partially-populated CCD is detected, a notice
banner explains this at startup.

## Features

- CCD count detected live from sysfs (via shared L3 cache slices) —
  no hardcoded topology.
- Fixed 8-slot-per-CCD grid, laid out in columns (one per CCD).
- Per-slot Disable checkbox — a disabled slot is skipped entirely when
  applying (no `--set-coper` sent for it).
- A single all-core offset (`--set-coall`).
- Primary-CCD-only / all-CCDs mode toggle.
- Profile save/load/delete (stored as JSON under
  `~/.config/ryzen-curve-optimizer/profiles/`).
- A compact output/log terminal showing every applied command and
  result.
- Gruvbox-themed, compact interface.

## How it applies changes

The GUI itself never runs as root. When you click an apply button, it
invokes a small, separate root helper script
(`ryzen_curve_optimizer_helper.py`) via `pkexec`, which:

- never uses `shell=True` — `ryzenadj` is always invoked with an
  explicit argument list;
- resolves the `ryzenadj` binary from a fixed candidate path list, not
  from your `$PATH`;
- clamps every numeric value it receives to a strict range before
  building the `ryzenadj` argument.

Because `pkexec` already prompts you for authentication before
anything runs, there is no separate "are you sure?" dialog in the
GUI — declining or cancelling the `pkexec` prompt **is** the cancel
action.

## Requirements

- Linux with `polkit` installed (for `pkexec`).
- [`ryzenadj`](https://github.com/FlyGoat/RyzenAdj) installed and on
  `PATH` (or in one of the fixed candidate locations the helper
  checks: `/usr/bin`, `/usr/local/bin`, `/usr/sbin`).
- Python 3.10+ with PySide6:
  ```bash
  pip install --user PySide6
  ```
  (or your distro's package, e.g. `dev-python/pyside` on Gentoo,
  `python3-pyside6` on Debian/Ubuntu).
- An AMD Ryzen Zen2/Zen3/Zen4 CPU. (This does not work on Intel CPUs
  or on Zen1.)

## Install

```bash
chmod +x install.sh
sudo ./install.sh
```

This installs:

| File                                  | Destination                                                    |
|----------------------------------------|-----------------------------------------------------------------|
| `ryzen_curve_optimizer_helper.py`      | `/usr/local/lib/ryzen-curve-optimizer/`                        |
| `icon.png`                              | `/usr/local/share/ryzen-curve-optimizer/`                      |
| `com.ryzencurveoptimizer.policy`        | `/usr/share/polkit-1/actions/`                                 |

The main GUI script (`ryzen_curve_optimizer.py`) is **not** installed
system-wide — run it directly from wherever you keep it:

```bash
python3 ryzen_curve_optimizer.py
```

## Uninstall

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh            # removes the helper, icon, and polkit policy; keeps saved profiles
sudo ./uninstall.sh --purge    # also deletes every user's saved profiles
```

## Files

- `ryzen_curve_optimizer.py` — the GUI application (run this one).
- `ryzen_curve_optimizer_helper.py` — the root helper invoked via
  `pkexec`; installed by `install.sh`, not run directly.
- `com.ryzencurveoptimizer.policy` — polkit policy granting the
  `pkexec` action used to run the helper.
- `install.sh` / `uninstall.sh` — installer/uninstaller for the helper,
  icon, and polkit policy.
- `icon.png` / `icon.svg` — application icon.

## License / attribution

This applet is independent of the
[m16R1-power-manager](https://github.com/arabcian/m16R1-power-manager)
project — it uses its own `pkexec` action and its own root helper, and
does not modify or depend on that project's files.

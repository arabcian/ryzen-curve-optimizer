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

- CCD count detected live from sysfs (by matching the level-3 cache
  and reading its shared CPU list) — no hardcoded topology.
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
  from your `$PATH`, and refuses it unless it is owned by `root` and
  not group/world-writable (running a user-writable binary as root
  would be a trivial privilege escalation);
- runs `ryzenadj` with a minimal, hard-coded environment;
- **rejects** any value that is not an integer inside the allowed
  range, rather than silently clamping it — a clamped value is a value
  you did not ask for, and quietly applying it to a voltage curve is
  worse than failing loudly;
- validates an entire per-core batch up front, so a single bad entry
  cannot leave your CPU with a half-applied curve.

Because `pkexec` already prompts you for authentication before
anything runs, there is no separate "are you sure?" dialog in the
GUI — declining or cancelling the `pkexec` prompt **is** the cancel
action.

## Requirements

- Linux with `polkit` installed (for `pkexec`).
- [`ryzenadj`](https://github.com/FlyGoat/RyzenAdj) installed in one of
  the fixed candidate locations the helper checks — `/usr/bin`,
  `/usr/sbin`, `/usr/local/bin`, `/usr/local/sbin`, `/opt/ryzenadj`.
  Your `$PATH` is never consulted. The binary must be root-owned and
  not group/world-writable; `install.sh` reports this for you.
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

This installs (default `PREFIX=/usr`):

| File                                | Destination                                                    |
|-------------------------------------|----------------------------------------------------------------|
| `ryzen_curve_optimizer.py`          | `/usr/bin/ryzen-curve-optimizer` (0755 root:root)              |
| `ryzen_curve_optimizer_helper.py`   | `/usr/lib/ryzen-curve-optimizer/` (0755 root:root)             |
| `icon.png`                          | `/usr/share/ryzen-curve-optimizer/` + hicolor 128x128          |
| `icon.svg`                          | `/usr/share/icons/hicolor/scalable/apps/`                      |
| `ryzen-curve-optimizer.desktop`     | `/usr/share/applications/`                                     |
| `com.ryzencurveoptimizer.policy`    | `/usr/share/polkit-1/actions/`                                 |

Then run it from anywhere:

```bash
ryzen-curve-optimizer
```

...or launch **Ryzen Curve Optimizer** from your application menu.

Useful flags:

```bash
ryzen-curve-optimizer --version
ryzen-curve-optimizer --detect    # print the detected CCD topology, no GUI
```

### Choosing a different prefix

```bash
sudo PREFIX=/usr/local ./install.sh   # install under /usr/local instead
DESTDIR=/tmp/stage ./install.sh       # staged install, for packaging
```

`install.sh` rewrites the polkit policy's `exec.path` annotation to
match wherever the helper actually lands. The polkit **action
directory** itself is always `/usr/share/polkit-1/actions` regardless
of `PREFIX`, because polkit's search path is compiled in.

> **Gentoo/portage note:** `/usr` belongs to the package manager. A
> plain `sudo ./install.sh` puts files there that portage does not
> track. Use `PREFIX=/usr/local`, or wrap this in an ebuild with
> `DESTDIR`, if you want `/usr` to stay clean.

## Uninstall

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh                    # removes everything; keeps saved profiles
sudo ./uninstall.sh --purge            # also deletes every user's saved profiles
sudo PREFIX=/usr/local ./uninstall.sh  # match a non-default install
```

It also cleans up leftovers from the older `/usr/local`-only layout
used by earlier versions.

## Files

- `ryzen_curve_optimizer.py` — the GUI application (run this one).
- `ryzen_curve_optimizer_helper.py` — the root helper invoked via
  `pkexec`; installed by `install.sh`, not run directly.
- `com.ryzencurveoptimizer.policy` — polkit policy granting the
  `pkexec` action used to run the helper.
- `ryzen-curve-optimizer.desktop` — application-menu entry.
- `install.sh` / `uninstall.sh` — installer/uninstaller.
- `icon.png` / `icon.svg` — application icon.

## License / attribution

This applet is independent of the
[m16R1-power-manager](https://github.com/arabcian/m16R1-power-manager)
project — it uses its own `pkexec` action and its own root helper, and
does not modify or depend on that project's files.

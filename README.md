# Ryzen Curve Optimizer

<img width="1456" height="901" alt="Screenshot_20261025_012625" src="https://github.com/user-attachments/assets/41ec87eb-a119-4b95-af9d-52ae6fdaa3e9" />

A small, standalone applet for tuning the AMD Ryzen Curve Optimizer on
Zen3/Zen4/Zen5 mobile and Dragon/Fire Range CPUs: a Qt6 GUI with 21 colour
themes, and a Rust root helper that talks to the CPU's SMU directly (no
`ryzenadj` needed).

![icon](icon.png)

Version 2 is the Curve Optimizer part of Centurion
(its Ryzen tab, root helper, polkit security model and theme system), extracted
into a package of its own. It replaces the earlier Python/PySide6/`ryzenadj`
version; `install.sh` migrates that install automatically (see
[Migrating from the Python version](#migrating-from-the-python-version)).

---

## ⚠️ Disclaimer

**Undervolting and Curve Optimizer changes can make your system
unstable, and in rare cases can damage hardware if pushed to extreme
values. Use this tool at your own risk.**

- This applet does not validate whether a given offset is *safe* for
  your specific chip — it only validates that the value is within the
  numeric range your CPU family allows (at most `-50`..`+20`) and correctly
  formatted. Whether a given offset is stable on your CPU is something only
  you can determine, through your own testing.
- Applying an offset that is too aggressive can cause crashes, kernel
  panics, corrupted data from an interrupted write, or a system that
  fails to boot until the Curve Optimizer is reset. It is unlikely
  but not impossible for sustained instability under load to damage
  hardware over time.
- The Curve Optimizer state applied by this tool is **not persistent across
  reboots** — it must be re-applied each boot (e.g. via a startup script/service
  of your own). This also means that if a value makes your system unbootable,
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

The SMU (System Management Unit) addresses cores by a **fixed 8-slot-per-CCD
layout** — every supported part addresses 8 slots per CCD, whether or not all 8
are physically populated on a given SKU. This is *not* necessarily the same
numbering as the OS's logical core IDs.

- On a full-core part (e.g. a 16-core/2×8 Ryzen 9 7845HX) the two
  happen to line up 1:1.
- On a part with disabled cores per CCD — e.g. a 6-core CCD used in
  some 12-core parts — only 6 of the 8 SMU slots are physically
  populated, and the OS only ever sees those 6. *Which* 6 of the 8 SMU
  slots they are cannot be discovered automatically by this or any
  other userspace tool.

So the applet always shows the full, fixed 8-slot-per-CCD grid and lets you
mark, via the **Disable** checkbox, any slot that doesn't correspond to a real
core on your chip. When a partially-populated CCD is detected, a notice banner
explains this.

## Features

- CCD count detected live from sysfs (by matching the level-3 cache
  and reading its shared CPU list) — no hardcoded topology. CCDs are ordered by
  L3 id (the die order the SMU follows); on X3D parts the V-Cache die is labelled.
- Fixed 8-slot-per-CCD grid, one card per CCD, with a per-CCD *Fill*.
- Per-slot Disable checkbox — a disabled slot is skipped entirely when applying.
- A single all-core offset, and a *Reset* that returns every offset to stock.
- Primary-CCD-only / all-CCDs mode toggle.
- Profile save/load/delete (JSON under `~/.config/ryzen-curve-optimizer/profiles/`).
- **Trial apply**: after each successful apply a 30 s countdown starts; unless you
  press *Keep*, the Curve Optimizer is reset to stock.
- Live monitor: clocks, temperatures (k10temp), boost state, per-core CPPC rank
  (★ marks the two best cores), and — with *VID + power* ticked — per-core VID
  volts and RAPL watts, plus which SMU limit (PPT/TDC/EDC/THM) is holding the
  clocks when `ryzen_smu` exports its PM table.
- 21 colour themes (see [Themes](#themes)).
- An output/log pane showing every applied command and result.

## Supported CPUs

The native SMU interface uses only mailbox addresses and opcodes confirmed by two
independent sources (the RyzenAdj source and the ZenTune/zenmaster tables). A CPU
outside this list is refused with a clear message instead of being guessed at:

| Family | Per-core offsets | Notes |
|---|---|---|
| Cezanne / Barcelo | yes | range `-30`..`+20` |
| Rembrandt, Phoenix, Hawk Point | yes | |
| Dragon Range / Raphael, Fire Range / Granite Ridge | yes | 2 CCDs |
| Strix Halo | yes | 2 CCDs |
| Strix Point, Krackan Point | all-core only | Zen5/Zen5c slot order is not defined by any verified source |

**Not supported in version 2:** Zen2 parts (no per-core CO in their firmware) and Zen3
desktop (Vermeer, Zen3 Threadripper/EPYC). The old Python version could drive some of
these through `ryzenadj`. `install.sh` therefore refuses to replace a working old
install on such a CPU — see below.

## How it applies changes

The GUI itself never runs as root. When you click an apply button, it runs
the root helper (`ryzen-curve-optimizer-helper`) through `pkexec`. The
protocol is one bounded JSON request on stdin, one JSON line on stdout.

- Nothing from argv or the environment is trusted; the helper clears its umask
  and core-dump limit first.
- **No `ryzenadj`, no shell.** The SMU is driven natively: family table, mailbox
  addresses and opcodes are fixed constants, and no opcode ever comes from the
  request.
- Transport, first that passes RyzenAdj's own self-test wins: the `ryzen_smu` driver's
  mailbox, then its raw `smn` window (both work under Secure Boot / kernel lockdown), then
  PCI config space of `00:00.0` (only without lockdown, and only after checking it is an
  AMD host bridge). Every bus transaction runs under an exclusive `flock` with a bounded
  wait, and the SMU's answer is polled for at most one second.
- **Rejects** any value that is not an integer inside the allowed range (also
  `true`, `-20.0`, `"-20"`), rather than silently clamping it — a clamped value is
  a value you did not ask for, and quietly applying it to a voltage curve is worse
  than failing loudly.
- A per-core batch is validated up front (ranges, duplicates, slots that exist on
  this family) and is **all-or-nothing**: it starts from stock, stops at the first
  failure, and rolls back, so a failure cannot leave your CPU with a half-applied
  curve.
- The helper is read-only for everything except the Curve Optimizer message
  itself; the live monitor reads MSRs read-only and ends when the GUI closes the pipe
  (with a 4 h ceiling).

Because `pkexec` already prompts you for authentication before anything runs,
there is no separate "are you sure?" dialog for apply — declining or cancelling
the `pkexec` prompt **is** the cancel action.

## Security model

One polkit action, `com.ryzencurveoptimizer.set-curve`, is pinned by `exec.path`
to the installed helper (root:root `0755`; pkexec refuses anything else). Who may
run it without a password is decided by a **security level** chosen at install time
(`--security-level=N`, asked interactively when not given, `RCO_SECURITY_LEVEL` also
works) and written into `/etc/polkit-1/rules.d/49-ryzen-curve-optimizer.rules`:

| Level | Behaviour |
|---|---|
| 1 | a `wheel` or `sudo` member at the machine (local, active session) applies without a password |
| 2 (default) | the same session is asked for the administrator password (polkit caches it briefly) |

Everyone else — SSH, another seat, a fast-user-switched session, service accounts —
is always asked, at every level. The rules file matches the action id against an
explicit allowlist (not a namespace prefix) and the helper path exactly; an unknown
`com.ryzencurveoptimizer.*` id gets a password prompt, never a silent grant. The
`.policy` defaults are the fallback if the rules file is missing: nobody is silent.
The prompt is not what keeps a bad value out — the helper re-validates every request
itself. An upgrade keeps the level of the installed rules file. Rerun `install.sh` with
`--security-level=N` to change it.

The live monitor (*VID + power*) runs through the same helper and action, so at level 2
it asks for the password when you switch it on. It is off by default.

## Requirements

- Linux with `polkit` (for `pkexec`).
- Qt 6.4+ (Widgets, Svg) at runtime.
- To **build**: a Rust toolchain (`cargo`), `cmake` and a C++20 compiler, plus the
  Qt6 development packages (Debian/Ubuntu: `qt6-base-dev libqt6svg6-dev`).
- SMU access: the [`ryzen_smu`](https://gitlab.com/leogx9r/ryzen_smu) kernel module
  (>= 0.1.7), required under Secure Boot / kernel lockdown; without lockdown a
  writable `/sys/bus/pci/devices/0000:00:00.0/config` also works.
- Optional: the `msr` module for the *VID + power* monitor.
- A supported AMD Ryzen CPU (see above). It does not work on Intel CPUs.

## Install

```bash
chmod +x install.sh
sudo ./install.sh
```

This builds the helper and the GUI and installs (default `PREFIX=/usr`):

| File | Destination |
|---|---|
| GUI | `/usr/bin/ryzen-curve-optimizer` (0755 root:root) |
| root helper | `/usr/lib/ryzen-curve-optimizer/ryzen-curve-optimizer-helper` (0755 root:root) |
| `icon.png` | `/usr/share/ryzen-curve-optimizer/` + hicolor 128x128 |
| `icon.svg` | `/usr/share/icons/hicolor/scalable/apps/` |
| `ryzen-curve-optimizer.desktop` | `/usr/share/applications/` |
| `com.ryzencurveoptimizer.policy` | `/usr/share/polkit-1/actions/` |
| `49-ryzen-curve-optimizer.rules` | `/etc/polkit-1/rules.d/` (`POLKIT_RULESDIR`) |

Then run it from anywhere:

```bash
ryzen-curve-optimizer
```

...or launch **Ryzen Curve Optimizer** from your application menu.

Useful flags:

```bash
ryzen-curve-optimizer --version
ryzen-curve-optimizer --detect    # print the detected CCD topology (JSON), no GUI
```

Installer options:

```bash
sudo PREFIX=/usr/local ./install.sh   # install under /usr/local instead
DESTDIR=/tmp/stage ./install.sh       # staged install, for packaging
sudo ./install.sh --no-build          # install an already built tree
sudo ./install.sh --security-level=1  # see Security model
```

`install.sh` rewrites the polkit policy's `exec.path` annotation to match wherever the
helper actually lands, and builds the GUI with the same path compiled in (a
`--no-build` install for a different `PREFIX` is refused). The polkit **action
directory** itself is always `/usr/share/polkit-1/actions` regardless of `PREFIX`,
because polkit's search path is compiled in.

> **Gentoo/portage note:** `/usr` belongs to the package manager. A
> plain `sudo ./install.sh` puts files there that portage does not
> track. Use `PREFIX=/usr/local`, or wrap this in an ebuild with
> `DESTDIR`, if you want `/usr` to stay clean.

### Migrating from the Python version

Run `install.sh` over the old install — no uninstall first. It detects the earlier
Python/`ryzenadj` version (the python script at `bin/ryzen-curve-optimizer`, the
`ryzen_curve_optimizer_helper.py` helper, a polkit action whose `exec.path` ends in
`.py`; looked for under `/usr`, `/usr/local` and your `PREFIX`) and then:

1. **Checks the CPU first.** If the old install is there but this version cannot
   drive the CPU (see Supported CPUs), it stops before touching anything.
2. Installs the new files, with the new polkit action (same id, new helper path) and
   rules (security level 2 unless you choose otherwise — what the old tool did).
3. Only then, after checking the new helper is root-owned `0755`, the new GUI is a real
   binary and polkit pins the action to the new helper, removes the old python script, helper,
   `__pycache__` and the old `/usr/local`-layout leftovers. Only things that are still
   python scripts / `.py` files are removed; the new install is never touched.
4. Leaves your **saved profiles** alone: they live in the same directory
   (`~/.config/ryzen-curve-optimizer/profiles/`) and use the same JSON, so they load
   as they are. The installer reports how many it found per user.

`ryzenadj` and PySide6 are no longer used and can be uninstalled. If an old Python window
is still open, close it and start the new one. Gruvbox, the old look, is one of the themes.

## Themes

The look is one source of truth (`gui/src/theme.cpp`): a palette of colour tokens
(surfaces, borders, text, amber-style accent, ok/warn/danger/info) and one style sheet
built from them, on top of Fusion. Pick a theme from the **Theme** box in the header; it
is saved to `~/.config/ryzen-curve-optimizer/gui.ini` and applied by restarting the app.

Graphite (default), Gruvbox, Tokyo Night, Horizon, Oxocarbon, Monokai Pro, Rosé Pine,
Kanagawa Dragon, Everforest, Synthwave '84, Crimson, Glacier, Ember, Mocha, Bordeaux,
Plum, Moss, Jade, Olive, Sakura, Phosphor.

A new palette is one more row of 19 colours in `PALETTES`.

*No mouse wheel on combo boxes* is deliberate: scrolling over a combo box never changes
its value (see `NoComboWheel` in `gui/src/main.cpp`).

## Uninstall

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh                    # removes everything; keeps saved profiles
sudo ./uninstall.sh --purge            # also deletes every user's profiles and settings
sudo PREFIX=/usr/local ./uninstall.sh  # match a non-default install
```

It also cleans up leftovers from the Python version. `--purge` refuses a home whose
`~/.config` is a symlink to somewhere else (root would otherwise delete there).

## Files

- `src/` — the root helper (Rust): `main.rs` protocol and validation, `smu.rs` native SMU
  mailbox, `ryzen_mon.rs` / `pmtable.rs` read-only monitor, `stream.rs`, `lib.rs` plumbing.
- `gui/` — the GUI (C++/Qt6): `ryzentab.cpp` main view, `theme.cpp` themes, `privileged.cpp`
  async `pkexec` runner, `trialbar.cpp` trial apply.
- `com.ryzencurveoptimizer.policy` — polkit action pinned to the helper.
- `49-ryzen-curve-optimizer.rules` — polkit rules (security level).
- `ryzen-curve-optimizer.desktop` — application-menu entry.
- `install.sh` / `uninstall.sh` — installer (with migration) / uninstaller.
- `icon.png` / `icon.svg` — application icon.

Tests: `cargo test` (SMU encodings, tables, ranges, mailbox behaviour against a fake SMU).

## License / attribution

GPL-3.0-or-later (see `LICENSE`), as the code is taken from Centurion; version 1 of this
applet was MIT. This applet is independent of the
[m16R1-power-manager](https://github.com/arabcian/m16R1-power-manager)
project — it uses its own `pkexec` action and its own root helper, and
does not modify or depend on that project's files. The SMU constants were
cross-checked against [RyzenAdj](https://github.com/FlyGoat/RyzenAdj); no code is copied from it.

Copyright © 2026 arabcian.

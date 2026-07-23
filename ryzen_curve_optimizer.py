#!/usr/bin/env python3
"""
ryzen_curve_optimizer.py — Ryzen Curve Optimizer

A small, standalone PySide6 tool for tuning the AMD Ryzen Curve
Optimizer (RyzenAdj --set-coall / --set-coper) on Zen-based CPUs
(built for/around the Alienware M16 R1 AMD, Ryzen 9 7845HX).

IMPORTANT — SMU slot IDs vs. OS core IDs:
  ryzenadj's --set-coper talks directly to the SMU (System Management
  Unit), which addresses cores by a FIXED per-CCD slot layout (8 slots
  per CCD on every consumer Zen2/Zen3/Zen4 part, regardless of how many
  of those slots are actually populated on a given SKU). This is NOT
  the same numbering as the OS's logical core IDs.

  On a full-core part (e.g. a 16-core/2x8 7845HX) the two happen to
  line up 1:1. But on a part with disabled cores per CCD — e.g. a
  6-core CCD (used in some 12-core parts) — only 6 of the 8 SMU slots
  are physically populated, and the OS only ever sees those 6. WHICH
  6 of the 8 SMU slots they are is not something this tool (or any
  userspace tool) can discover automatically — it depends on which
  physical cores AMD's binning process disabled on that specific chip.

  So: this applet always shows the full, fixed 8-slot-per-CCD grid
  (the real SMU addressing space), and lets the user manually mark
  which slots don't correspond to a real core on their chip via the
  Disable checkbox — figuring out which physical cores are actually
  populated is left to the user (typically worked out via matching
  core-specific load/temperature behaviour between `htop`/`corefreq`
  and this tool while testing).

Features:
  * CCD *count* is detected live from sysfs (reliable — CCD boundaries
    are exposed via shared L3 cache slices). Falls back to a 2-CCD
    guess if that can't be read.
  * Each CCD is always shown as a fixed 8-slot SMU addressing grid —
    the grid never shrinks based on how many cores the OS reports,
    since the SMU's own addressing space doesn't shrink either.
  * A warning banner appears when the OS-visible physical core count
    per CCD isn't 8, explaining the SMU-vs-OS mismatch above.
  * Manual per-slot CO offset entry, laid out as columns — one column
    per detected CCD.
  * Per-slot "disable" checkbox (to the right of the offset field, with
    a tooltip) — marks a slot as not populated on this chip. A disabled
    slot's offset field is cleared and locked, and the slot is skipped
    entirely when applying (no --set-coper sent for it).
  * Primary-CCD / All-CCDs mode toggle — in primary mode every other
    CCD's column is fully hidden (not just its rows).
  * A single all-core offset (--set-coall)
  * Profile save/load/delete (JSON, ~/.config/ryzen-curve-optimizer/profiles/)
  * A compact output/log terminal showing every applied command and result
  * Root operations are delegated via pkexec to a separate root helper
    script (ryzen_curve_optimizer_helper.py) — this GUI process never runs as root and
    never calls ryzenadj directly itself. Since pkexec already prompts
    for authentication before anything runs, there is no separate
    "are you sure?" dialog — declining/cancelling the pkexec prompt IS
    the cancel action.

This applet is independent of the m16R1-power-manager project's
root_helper.py architecture; it uses its own pkexec action and its own
tiny helper — it does not touch the main project.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QPlainTextEdit, QCheckBox,
    QButtonGroup, QRadioButton, QMessageBox, QInputDialog, QComboBox,
    QGroupBox, QFrame,
)

APP_TITLE = "Ryzen Curve Optimizer"

# Fixed SMU addressing space: every consumer/mobile Zen2/3/4 part
# addresses 8 core slots per CCD, whether or not all 8 are populated.
SLOTS_PER_CCD = 8
CCD_COUNT_FALLBACK = 2

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ryzen-curve-optimizer"
PROFILES_DIR = CONFIG_DIR / "profiles"
ICON_CANDIDATES = [
    "/usr/local/share/ryzen-curve-optimizer/icon.png",
    str(Path(__file__).resolve().parent / "icon_128.png"),
    str(Path(__file__).resolve().parent / "icon.png"),
]

HELPER_CANDIDATES = [
    "/usr/local/lib/ryzen-curve-optimizer/ryzen_curve_optimizer_helper.py",
    str(Path(__file__).resolve().parent / "ryzen_curve_optimizer_helper.py"),
]

COALL_MIN, COALL_MAX = -50, 20
COPER_MIN, COPER_MAX = -50, 20

# ─────────────────────────────────────────────── Gruvbox palette ──────
GB_BG_HARD = "#1d2021"
GB_BG0 = "#282828"
GB_BG0_SOFT = "#32302f"
GB_BG1 = "#3c3836"
GB_BG2 = "#504945"
GB_BG3 = "#665c54"
GB_BG4 = "#7c6f64"
GB_FG1 = "#ebdbb2"
GB_FG2 = "#d5c4a1"
GB_FG3 = "#bdae93"
GB_FG4 = "#a89984"
GB_GREY = "#928374"

GB_RED = "#fb4934"
GB_RED_DIM = "#cc241d"
GB_GREEN = "#b8bb26"
GB_GREEN_DIM = "#98971a"
GB_YELLOW = "#fabd2f"
GB_YELLOW_DIM = "#d79921"
GB_BLUE = "#83a598"
GB_BLUE_DIM = "#458588"
GB_PURPLE = "#d3869b"
GB_PURPLE_DIM = "#b16286"
GB_AQUA = "#8ec07c"
GB_AQUA_DIM = "#689d6a"
GB_ORANGE = "#fe8019"
GB_ORANGE_DIM = "#d65d0e"


# ════════════════════════════════════════════ CPU topology detection ═
def detect_ccd_layout() -> dict:
    """Reads what CAN be reliably read from sysfs: the number of CCDs,
    and how many OS-visible physical cores each CCD actually has.

    Returns:
        {
            "ccd_count": int,
            "os_cores_per_ccd": {ccd_index: int, ...},
        }

    CCD boundaries come from shared L3 cache slices
    (/sys/devices/system/cpu/cpu*/cache/index3/shared_cpu_list), which
    is reliable and requires no root. Physical-core counting within a
    CCD collapses SMT sibling threads via topology/core_cpus_list.

    This does NOT attempt to guess which of the 8 fixed SMU slots per
    CCD those OS-visible cores correspond to — see the module
    docstring for why that mapping cannot be auto-detected. It is only
    used to decide (a) how many CCD columns to show and (b) whether to
    display the SMU-vs-OS mismatch warning (shown whenever a CCD's
    OS-visible core count isn't exactly SLOTS_PER_CCD).

    Returns {"ccd_count": 0, "os_cores_per_ccd": {}} if nothing usable
    could be read — callers should fall back to a fixed guess in that
    case.
    """
    try:
        cpu_dirs = sorted(
            glob.glob("/sys/devices/system/cpu/cpu[0-9]*"),
            key=lambda p: int(p.rsplit("cpu", 1)[1]),
        )
    except (OSError, ValueError):
        return {"ccd_count": 0, "os_cores_per_ccd": {}}
    if not cpu_dirs:
        return {"ccd_count": 0, "os_cores_per_ccd": {}}

    def _read(path: str) -> str | None:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None

    def _expand_list(s: str) -> list[int]:
        cpus = []
        for part in s.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    lo, hi = part.split("-", 1)
                    cpus.extend(range(int(lo), int(hi) + 1))
                except ValueError:
                    continue
            else:
                try:
                    cpus.append(int(part))
                except ValueError:
                    continue
        return cpus

    # Group logical CPUs by shared L3 slice (= CCD boundary).
    l3_groups: dict[str, list[int]] = {}
    for cpu_dir in cpu_dirs:
        shared = _read(f"{cpu_dir}/cache/index3/shared_cpu_list")
        if shared is None:
            continue
        l3_groups.setdefault(shared, [])

    if not l3_groups:
        return {"ccd_count": 0, "os_cores_per_ccd": {}}

    for shared_key in l3_groups:
        l3_groups[shared_key] = sorted(set(_expand_list(shared_key)))

    def _first(cpus: list[int]) -> int:
        return cpus[0] if cpus else 1 << 30

    ordered_ccds = sorted(l3_groups.values(), key=_first)

    os_cores_per_ccd: dict[int, int] = {}
    for ccd_idx, ccd_cpus in enumerate(ordered_ccds):
        # Collapse SMT siblings to one physical core each.
        seen_phys: set[str] = set()
        for cpu in ccd_cpus:
            sib = _read(f"/sys/devices/system/cpu/cpu{cpu}/topology/core_cpus_list")
            if sib is None:
                sib = _read(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
            seen_phys.add(sib if sib else str(cpu))
        os_cores_per_ccd[ccd_idx] = len(seen_phys)

    return {"ccd_count": len(ordered_ccds), "os_cores_per_ccd": os_cores_per_ccd}


def _find_helper() -> str | None:
    for cand in HELPER_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def _find_icon() -> str | None:
    for cand in ICON_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def _find_pkexec() -> str | None:
    for cand in ("/usr/bin/pkexec", "/usr/local/bin/pkexec"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    import shutil
    return shutil.which("pkexec")


class HelperWorker(QThread):
    """Runs the pkexec + root helper call on a background thread so the
    UI never blocks. Emits finished_ok(result_dict) or finished_err(str).

    No separate confirmation dialog precedes this — pkexec's own
    authentication prompt is the confirmation step; declining/cancelling
    it is how the user backs out."""

    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(self, op: str, params: dict, parent=None):
        super().__init__(parent)
        self.op = op
        self.params = params

    def run(self):
        pkexec = _find_pkexec()
        if not pkexec:
            self.finished_err.emit("pkexec not found. Is polkit installed?")
            return
        helper = _find_helper()
        if not helper:
            self.finished_err.emit(
                "ryzen_curve_optimizer_helper.py not found.\n"
                "Expected at: /usr/local/lib/ryzen-curve-optimizer/ryzen_curve_optimizer_helper.py\n"
                "(or next to this script)."
            )
            return

        payload = json.dumps({"op": self.op, "params": self.params})

        try:
            proc = subprocess.run(
                [pkexec, sys.executable, helper],
                input=payload,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            self.finished_err.emit("Operation timed out (pkexec/helper did not respond within 20s).")
            return
        except Exception as e:  # noqa: BLE001
            self.finished_err.emit(f"Failed to run pkexec: {e}")
            return

        if proc.returncode in (126, 127):
            # 126: user dismissed/declined the polkit prompt — i.e. cancel
            # 127: pkexec/command not found
            self.finished_err.emit(
                "Cancelled." if proc.returncode == 126
                else f"pkexec/command not found (exit {proc.returncode})."
            )
            return

        out = (proc.stdout or "").strip()
        if not out:
            self.finished_err.emit(
                f"No output from helper (exit {proc.returncode}).\n"
                f"stderr: {(proc.stderr or '').strip()}"
            )
            return

        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            self.finished_err.emit(f"Helper returned invalid output: {out[:500]}")
            return

        self.finished_ok.emit(result)


DISABLE_TOOLTIP = (
    "Disable this SMU slot.\n"
    "Check this if your CPU does not actually have a physical core at "
    "this slot (e.g. a 6-core CCD, where only 6 of the 8 SMU slots are "
    "populated). Disabled slots are skipped entirely — no CO offset is "
    "sent for them."
)


class CoreRow(QWidget):
    """One fixed SMU slot: slot ID, the CO offset entry, and (to its
    right) the disable checkbox."""

    def __init__(self, ccd: int, ccx: int, slot: int, accent: str, parent=None):
        super().__init__(parent)
        self.ccd, self.ccx, self.slot = ccd, ccx, slot

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(4)

        lbl_id = QLabel(f"S{slot}")
        lbl_id.setFixedWidth(24)
        lbl_id.setToolTip(f"Fixed SMU slot {slot} of CCD{ccd} (hardware addressing, not the OS core id)")
        lbl_id.setStyleSheet(f"color:{accent}; font-weight:600;")

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("0")
        self.entry.setFixedWidth(40)
        self.entry.setFixedHeight(22)
        self.entry.setAlignment(Qt.AlignCenter)

        self.disable_cb = QCheckBox()
        self.disable_cb.setToolTip(DISABLE_TOOLTIP)
        self.disable_cb.setFixedWidth(18)
        self.disable_cb.toggled.connect(self._on_disable_toggled)

        lay.addWidget(lbl_id)
        lay.addWidget(self.entry)
        lay.addWidget(self.disable_cb)
        lay.addStretch()

    def _on_disable_toggled(self, checked: bool):
        self.entry.setEnabled(not checked)
        if checked:
            self.entry.clear()

    def is_disabled(self) -> bool:
        return self.disable_cb.isChecked()

    def value(self) -> int | None:
        if self.is_disabled():
            return None
        txt = self.entry.text().strip()
        if not txt:
            return None
        try:
            v = int(txt)
        except ValueError:
            return None
        return max(COPER_MIN, min(COPER_MAX, v))

    def set_value(self, v):
        self.entry.setText("" if v is None else str(v))

    def set_disabled_state(self, disabled: bool):
        self.disable_cb.setChecked(disabled)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(520, 640)

        icon_path = _find_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        PROFILES_DIR.mkdir(parents=True, exist_ok=True)

        layout_info = detect_ccd_layout()
        self.ccd_count = layout_info["ccd_count"] or CCD_COUNT_FALLBACK
        self.os_cores_per_ccd: dict[int, int] = layout_info["os_cores_per_ccd"]
        self.ccds = list(range(self.ccd_count))
        self.total_slots = self.ccd_count * SLOTS_PER_CCD

        # Any CCD whose OS-visible physical core count isn't the full
        # 8 SMU slots means real-vs-OS core id mapping is ambiguous —
        # this drives the warning banner.
        self.mismatch_ccds = [
            ccd for ccd in self.ccds
            if ccd in self.os_cores_per_ccd and self.os_cores_per_ccd[ccd] != SLOTS_PER_CCD
        ]

        self.core_rows: list[CoreRow] = []
        self._worker: HelperWorker | None = None
        self._ccd_columns: dict[int, QWidget] = {}
        self._ccd_separators: dict[int, QFrame] = {}

        self._build_ui()
        self._apply_theme()
        self._set_core_mode(all_ccds=True)

    # ─────────────────────────────────────────── UI construction ──────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Optional SMU-vs-OS mismatch warning ──
        if self.mismatch_ccds:
            mismatch_desc = ", ".join(
                f"CCD{ccd}: {self.os_cores_per_ccd.get(ccd, '?')}/{SLOTS_PER_CCD} cores"
                for ccd in self.mismatch_ccds
            )
            warn_box = QGroupBox("Notice")
            warn_box.setObjectName("box_yellow")
            warn_lay = QVBoxLayout(warn_box)
            warn_lay.setContentsMargins(8, 6, 8, 6)
            warn_label = QLabel(
                f"Detected a partially-populated CCD ({mismatch_desc}). "
                "On chips like this, the SMU's 8 fixed core slots per CCD "
                "and the OS's core numbering do not necessarily line up "
                "1:1 — some slots may not correspond to a real core at "
                "all, and this cannot be auto-detected. Check Disable for "
                "any slot with no real core behind it."
            )
            warn_label.setWordWrap(True)
            warn_label.setStyleSheet(f"color:{GB_FG2};")
            warn_lay.addWidget(warn_label)
            root.addWidget(warn_box)

        # ── Top bar: core mode + profile controls ──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        mode_box = QGroupBox("Core Mode")
        mode_box.setObjectName("box_blue")
        mode_lay = QHBoxLayout(mode_box)
        mode_lay.setContentsMargins(8, 6, 8, 4)
        mode_lay.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.radio_primary = QRadioButton(f"CCD0 Only ({SLOTS_PER_CCD} slots)")
        self.radio_all = QRadioButton(f"All CCDs ({self.total_slots} slots)")
        self.radio_all.setChecked(True)
        self.mode_group.addButton(self.radio_primary)
        self.mode_group.addButton(self.radio_all)
        self.radio_primary.toggled.connect(
            lambda checked: checked and self._set_core_mode(all_ccds=False))
        self.radio_all.toggled.connect(
            lambda checked: checked and self._set_core_mode(all_ccds=True))
        mode_lay.addWidget(self.radio_primary)
        mode_lay.addWidget(self.radio_all)
        if self.ccd_count < 2:
            mode_box.setVisible(False)
        top_bar.addWidget(mode_box)

        profile_box = QGroupBox("Profile")
        profile_box.setObjectName("box_purple")
        profile_lay = QHBoxLayout(profile_box)
        profile_lay.setContentsMargins(8, 6, 8, 4)
        profile_lay.setSpacing(4)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(110)
        self._reload_profile_list()
        btn_load = QPushButton("Load")
        btn_load.clicked.connect(self._load_profile)
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save_profile)
        btn_delete = QPushButton("Del")
        btn_delete.setObjectName("btn_danger")
        btn_delete.clicked.connect(self._delete_profile)
        profile_lay.addWidget(self.profile_combo, stretch=1)
        profile_lay.addWidget(btn_load)
        profile_lay.addWidget(btn_save)
        profile_lay.addWidget(btn_delete)
        top_bar.addWidget(profile_box, stretch=1)

        root.addLayout(top_bar)

        # ── All-core offset ──
        allcore_box = QGroupBox("All-Core Offset  (--set-coall)")
        allcore_box.setObjectName("box_yellow")
        allcore_lay = QHBoxLayout(allcore_box)
        allcore_lay.setContentsMargins(8, 6, 8, 4)
        allcore_lay.setSpacing(6)
        self.coall_entry = QLineEdit()
        self.coall_entry.setPlaceholderText(f"{COALL_MIN}..{COALL_MAX}")
        self.coall_entry.setFixedWidth(90)
        btn_apply_all = QPushButton("Apply All-Core")
        btn_apply_all.setObjectName("btn_accent")
        btn_apply_all.clicked.connect(self._apply_all_core)
        btn_reset = QPushButton("Reset")
        btn_reset.setObjectName("btn_danger")
        btn_reset.clicked.connect(self._apply_reset)
        allcore_lay.addWidget(self.coall_entry)
        allcore_lay.addWidget(btn_apply_all)
        allcore_lay.addStretch()
        allcore_lay.addWidget(btn_reset)
        root.addWidget(allcore_box)

        # ── Per-slot columns, one per detected CCD ──
        core_box = QGroupBox("Per-Core Curve Optimizer Offsets  (fixed SMU slot layout)")
        core_box.setObjectName("box_green")
        core_box_lay = QVBoxLayout(core_box)
        core_box_lay.setContentsMargins(8, 6, 8, 6)
        core_box_lay.setSpacing(4)

        core_header = QHBoxLayout()
        hdr_hint = QLabel("slot   offset   dis")
        hdr_hint.setStyleSheet(f"color:{GB_GREY}; font-size:9px;")
        core_header.addWidget(hdr_hint)
        core_header.addStretch()
        btn_clear = QPushButton("Clear")
        btn_apply_cores = QPushButton("Apply Per-Core")
        btn_apply_cores.setObjectName("btn_accent")
        btn_clear.clicked.connect(self._clear_core_fields)
        btn_apply_cores.clicked.connect(self._apply_per_core)
        core_header.addWidget(btn_clear)
        core_header.addWidget(btn_apply_cores)
        core_box_lay.addLayout(core_header)

        columns = QHBoxLayout()
        columns.setSpacing(10)

        ccd_accents = [GB_AQUA, GB_ORANGE, GB_PURPLE, GB_BLUE]
        for col_i, ccd_id in enumerate(self.ccds):
            accent = ccd_accents[col_i % len(ccd_accents)]
            col_lay = QVBoxLayout()
            col_lay.setSpacing(1)
            lbl = QLabel(f"CCD{ccd_id}")
            lbl.setStyleSheet(f"color:{accent}; font-weight:bold;")
            col_lay.addWidget(lbl)

            for slot in range(SLOTS_PER_CCD):
                row = CoreRow(ccd_id, 0, slot, accent)
                self.core_rows.append(row)
                col_lay.addWidget(row)

            col_lay.addStretch()
            col_widget = QWidget()
            col_widget.setLayout(col_lay)
            self._ccd_columns[ccd_id] = col_widget
            columns.addWidget(col_widget)

            if col_i < len(self.ccds) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color:{GB_BG3};")
                columns.addWidget(sep)
                # This separator sits between the current column and the
                # NEXT one, so its visibility should follow the next
                # CCD's visibility (hide the divider when the CCD after
                # it disappears), not the current CCD's.
                next_ccd_id = self.ccds[col_i + 1]
                self._ccd_separators[next_ccd_id] = sep

        core_box_lay.addLayout(columns)
        root.addWidget(core_box)

        # ── Output terminal ──
        term_box = QGroupBox("Output / Log")
        term_box.setObjectName("box_grey")
        term_lay = QVBoxLayout(term_box)
        term_lay.setContentsMargins(6, 4, 6, 4)
        self.terminal = QPlainTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Monospace", 8))
        self.terminal.setObjectName("terminal")
        term_lay.addWidget(self.terminal)
        root.addWidget(term_box, stretch=1)

        self._log(f"Ryzen Curve Optimizer started. {self.ccd_count} CCD(s) detected, "
                   f"{SLOTS_PER_CCD} fixed SMU slots each ({self.total_slots} total).")
        if self.mismatch_ccds:
            self._log("Partially-populated CCD detected — see the notice above "
                       "about SMU slot IDs vs. OS core IDs.", "cmd")
        self._log("Changes are applied via pkexec through the root helper — "
                   "the pkexec prompt itself is your confirm/cancel step.")

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {GB_BG_HARD};
                color: {GB_FG2};
                font-size: 11px;
            }}
            QGroupBox {{
                border: 1px solid {GB_BG3};
                border-radius: 5px;
                margin-top: 7px;
                font-weight: bold;
                font-size: 10px;
                color: {GB_FG3};
                background-color: {GB_BG0_SOFT};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 7px;
                padding: 0 4px;
            }}
            QGroupBox#box_blue::title {{ color: {GB_BLUE}; }}
            QGroupBox#box_purple::title {{ color: {GB_PURPLE}; }}
            QGroupBox#box_yellow::title {{ color: {GB_YELLOW}; }}
            QGroupBox#box_green::title {{ color: {GB_AQUA}; }}
            QGroupBox#box_grey::title {{ color: {GB_GREY}; }}

            QGroupBox#box_blue {{ border-color: {GB_BLUE_DIM}; }}
            QGroupBox#box_purple {{ border-color: {GB_PURPLE_DIM}; }}
            QGroupBox#box_yellow {{ border-color: {GB_YELLOW_DIM}; }}
            QGroupBox#box_green {{ border-color: {GB_AQUA_DIM}; }}
            QGroupBox#box_grey {{ border-color: {GB_BG3}; }}

            QLineEdit, QComboBox {{
                background-color: {GB_BG1};
                border: 1px solid {GB_BG3};
                border-radius: 3px;
                color: {GB_FG1};
                padding: 2px 4px;
            }}
            QLineEdit:focus {{ border: 1px solid {GB_YELLOW_DIM}; }}
            QLineEdit:disabled {{ color: {GB_BG4}; background-color: {GB_BG0}; }}

            QPlainTextEdit#terminal {{
                background-color: {GB_BG_HARD};
                border: 1px solid {GB_BG2};
                border-radius: 3px;
                color: {GB_GREEN};
            }}

            QPushButton {{
                background-color: {GB_BG2};
                border: 1px solid {GB_BG3};
                border-radius: 4px;
                padding: 4px 9px;
                color: {GB_FG1};
            }}
            QPushButton:hover {{ background-color: {GB_BG3}; }}
            QPushButton:pressed {{ background-color: {GB_BG1}; }}

            QPushButton#btn_accent {{
                background-color: {GB_AQUA_DIM};
                border: 1px solid {GB_AQUA};
                color: {GB_BG_HARD};
                font-weight: 600;
            }}
            QPushButton#btn_accent:hover {{ background-color: {GB_AQUA}; }}

            QPushButton#btn_danger {{
                background-color: {GB_BG1};
                border: 1px solid {GB_RED_DIM};
                color: {GB_RED};
            }}
            QPushButton#btn_danger:hover {{ background-color: {GB_RED_DIM}; color: {GB_FG1}; }}

            QRadioButton {{ spacing: 5px; color: {GB_FG2}; }}
            QCheckBox {{ spacing: 0px; }}
            QLabel {{ color: {GB_FG2}; }}

            QScrollBar:vertical {{
                background: {GB_BG0}; width: 9px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {GB_BG3}; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    # ─────────────────────────────────────────── log/terminal ─────────
    def _log(self, msg: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {
            "info": GB_FG3, "ok": GB_GREEN, "err": GB_RED, "cmd": GB_YELLOW,
        }
        prefix = {"info": "  ", "ok": "OK", "err": "!!", "cmd": ">>"}.get(level, "  ")
        color = colors.get(level, GB_FG3)
        self.terminal.appendHtml(
            f'<span style="color:{GB_GREY};">[{ts}]</span> '
            f'<span style="color:{color};">{prefix} {msg}</span>'
        )
        sb = self.terminal.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ─────────────────────────────────────────── mode (primary/all) ───
    def _set_core_mode(self, all_ccds: bool):
        active_ccds = set(self.ccds) if all_ccds else {self.ccds[0]} if self.ccds else set()

        for ccd_id, widget in self._ccd_columns.items():
            visible = ccd_id in active_ccds
            widget.setVisible(visible)
            sep = self._ccd_separators.get(ccd_id)
            if sep is not None:
                sep.setVisible(visible)

        for row in self.core_rows:
            if row.ccd not in active_ccds:
                row.set_value(None)
                row.set_disabled_state(False)

        label = f"CCD0 Only ({SLOTS_PER_CCD} slots)" if not all_ccds else f"All CCDs ({self.total_slots} slots)"
        self._log(f"Core mode: {label}")

    def _active_rows(self) -> list[CoreRow]:
        visible_ccds = {ccd for ccd, w in self._ccd_columns.items() if w.isVisible()}
        if not visible_ccds:
            return list(self.core_rows)
        return [r for r in self.core_rows if r.ccd in visible_ccds]

    def _clear_core_fields(self):
        for row in self._active_rows():
            row.set_value(None)
            row.set_disabled_state(False)
        self._log("Per-core fields cleared.")

    # ─────────────────────────────────────────── apply actions ────────
    def _apply_all_core(self):
        txt = self.coall_entry.text().strip()
        if not txt:
            QMessageBox.warning(self, "Missing Value", "Please enter an all-core offset value.")
            return
        try:
            value = int(txt)
        except ValueError:
            QMessageBox.warning(self, "Invalid Value", "All-core offset must be an integer.")
            return
        if not (COALL_MIN <= value <= COALL_MAX):
            QMessageBox.warning(self, "Out of Range", f"Offset must be within {COALL_MIN}..{COALL_MAX}.")
            return
        self._run_op("set_coall", {"value": value})

    def _apply_reset(self):
        self._run_op("reset", {})

    def _apply_per_core(self):
        entries = []
        skipped_disabled = 0
        for row in self._active_rows():
            if row.is_disabled():
                skipped_disabled += 1
                continue
            v = row.value()
            if v is None:
                continue
            entries.append({"ccd": row.ccd, "ccx": row.ccx, "core": row.slot, "coper": v})
        if not entries:
            QMessageBox.information(self, "No Input", "No per-core values have been entered.")
            return
        if skipped_disabled:
            self._log(f"Skipping {skipped_disabled} disabled slot(s).")
        self._run_op("set_coper_batch", {"entries": entries})

    def _run_op(self, op: str, params: dict):
        if self._worker is not None and self._worker.isRunning():
            self._log("Previous operation still running, please wait.", "err")
            return

        self._log(f"Sending '{op}' via pkexec... (you may be prompted for a password)", "cmd")
        self._worker = HelperWorker(op, params)
        self._worker.finished_ok.connect(self._on_op_ok)
        self._worker.finished_err.connect(self._on_op_err)
        self._worker.start()

    def _on_op_ok(self, result: dict):
        if result.get("ok"):
            self._log("Operation applied successfully.", "ok")
        else:
            self._log(f"Operation partially/fully failed: {result.get('error', '')}", "err")

        if "results" in result:
            for r in result["results"]:
                tag = "ok" if r.get("ok") else "err"
                self._log(
                    f"CCD{r.get('ccd')}/Slot{r.get('core')} "
                    f"-> offset {r.get('coper')}  ({r.get('message', '')})",
                    tag,
                )
        elif result.get("message"):
            self._log(result["message"])

    def _on_op_err(self, err: str):
        self._log(err, "err")

    # ─────────────────────────────────────────── profile save/load ────
    def _current_state(self) -> dict:
        active = self._active_rows()
        cores = []
        for row in active:
            cores.append({
                "ccd": row.ccd, "ccx": row.ccx, "slot": row.slot,
                "coper": row.value(), "disabled": row.is_disabled(),
            })
        coall_txt = self.coall_entry.text().strip()
        coall = None
        if coall_txt.lstrip("-").isdigit():
            coall = int(coall_txt)
        return {
            "coall": coall,
            "cores": cores,
        }

    def _reload_profile_list(self):
        self.profile_combo.clear()
        try:
            names = sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
        except OSError:
            names = []
        self.profile_combo.addItems(names)

    def _save_profile(self):
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        safe_name = "".join(c for c in name if c.isalnum() or c in "_- ")
        if not safe_name:
            QMessageBox.warning(self, "Invalid Name", "Profile name contains invalid characters.")
            return

        path = PROFILES_DIR / f"{safe_name}.json"
        try:
            path.write_text(json.dumps(self._current_state(), indent=2, ensure_ascii=False))
        except OSError as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        self._reload_profile_list()
        idx = self.profile_combo.findText(safe_name)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self._log(f"Profile saved: {path}")

    def _load_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            QMessageBox.information(self, "No Profile", "No profile selected to load.")
            return
        path = PROFILES_DIR / f"{name}.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        coall = data.get("coall")
        self.coall_entry.setText("" if coall is None else str(coall))

        by_pos = {(r.ccd, r.ccx, r.slot): r for r in self.core_rows}
        for row in self.core_rows:
            row.set_value(None)
            row.set_disabled_state(False)
        for c in data.get("cores", []):
            # Accept both the new "slot" key and the older "core" key
            # (profiles saved by an earlier version of this applet).
            slot = c.get("slot", c.get("core", 0))
            key = (c.get("ccd", 0), c.get("ccx", 0), slot)
            row = by_pos.get(key)
            if row is None:
                continue
            row.set_disabled_state(bool(c.get("disabled", False)))
            if not c.get("disabled", False):
                row.set_value(c.get("coper"))

        self._log(f"Profile loaded: {name}")

    def _delete_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        reply = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        path = PROFILES_DIR / f"{name}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Delete Error", str(e))
            return
        self._reload_profile_list()
        self._log(f"Profile deleted: {name}")


def main():
    app = QApplication(sys.argv)
    icon_path = _find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

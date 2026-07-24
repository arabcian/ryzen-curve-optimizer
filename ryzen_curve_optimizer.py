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
  Disable checkbox.

Root operations are delegated via pkexec to a separate root helper
script; this GUI process never runs as root and never calls ryzenadj
directly. pkexec's own authentication prompt IS the confirmation step —
declining it is how the user backs out.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon, QIntValidator, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QPlainTextEdit, QCheckBox,
    QButtonGroup, QRadioButton, QMessageBox, QInputDialog, QComboBox,
    QGroupBox, QFrame,
)

APP_ID = "ryzen-curve-optimizer"
APP_TITLE = "Ryzen Curve Optimizer"
APP_VERSION = "1.1.0"

# Fixed SMU addressing space: every consumer/mobile Zen2/3/4 part
# addresses 8 core slots per CCD, whether or not all 8 are populated.
SLOTS_PER_CCD = 8
CCD_COUNT_FALLBACK = 2

CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
) / APP_ID
PROFILES_DIR = CONFIG_DIR / "profiles"

_HERE = Path(__file__).resolve().parent

# System prefix first, then the source tree (so the applet still runs
# straight out of a git checkout without being installed).
ICON_CANDIDATES = [
    f"/usr/share/{APP_ID}/icon.png",
    f"/usr/share/icons/hicolor/128x128/apps/{APP_ID}.png",
    f"/usr/local/share/{APP_ID}/icon.png",
    str(_HERE / "icon.png"),
]

HELPER_NAME = "ryzen_curve_optimizer_helper.py"
HELPER_CANDIDATES = [
    f"/usr/lib/{APP_ID}/{HELPER_NAME}",
    f"/usr/libexec/{APP_ID}/{HELPER_NAME}",
    f"/usr/local/lib/{APP_ID}/{HELPER_NAME}",
    str(_HERE / HELPER_NAME),
]

COALL_MIN, COALL_MAX = -50, 20
COPER_MIN, COPER_MAX = -50, 20

# Generous: this budget has to cover the user reading and typing into
# the polkit password prompt, not just the ryzenadj call. The old 20s
# meant a slow typist got a bogus "timed out" error.
PKEXEC_TIMEOUT = 300

MAX_LOG_BLOCKS = 2000

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")

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
GB_GREY = "#928374"

GB_RED = "#fb4934"
GB_RED_DIM = "#cc241d"
GB_GREEN = "#b8bb26"
GB_YELLOW = "#fabd2f"
GB_YELLOW_DIM = "#d79921"
GB_BLUE = "#83a598"
GB_BLUE_DIM = "#458588"
GB_PURPLE = "#d3869b"
GB_PURPLE_DIM = "#b16286"
GB_AQUA = "#8ec07c"
GB_AQUA_DIM = "#689d6a"
GB_ORANGE = "#fe8019"


# ════════════════════════════════════════════ CPU topology detection ═
def _read_sysfs(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _expand_cpu_list(s: str) -> list[int]:
    """Expands a sysfs cpu list ("0-3,8,12-15") into individual ids."""
    cpus: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            try:
                cpus.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                continue
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                continue
    return cpus


def _l3_shared_list(cpu_dir: str) -> str | None:
    """Returns the shared_cpu_list of the CPU's level-3 cache.

    The old code hard-coded cache/index3, which is only the L3 on the
    common x86 layout (L1d, L1i, L2, L3). Walking the cache indices and
    matching on `level` works everywhere, including parts that expose an
    L4/MALL slice or omit an index.
    """
    for cache_dir in sorted(glob.glob(f"{cpu_dir}/cache/index[0-9]*")):
        if _read_sysfs(f"{cache_dir}/level") == "3":
            return _read_sysfs(f"{cache_dir}/shared_cpu_list")
    return None


def detect_ccd_layout() -> dict:
    """Reads what CAN be reliably read from sysfs: the number of CCDs,
    and how many OS-visible physical cores each CCD actually has.

    Returns {"ccd_count": int, "os_cores_per_ccd": {ccd_index: int}}.

    CCD boundaries come from shared L3 cache slices, which is reliable
    and requires no root. Physical-core counting within a CCD collapses
    SMT sibling threads.

    This does NOT attempt to guess which of the 8 fixed SMU slots per
    CCD those OS-visible cores correspond to — see the module docstring
    for why that mapping cannot be auto-detected. Returns zeroes if
    nothing usable could be read; callers fall back to a fixed guess.
    """
    empty = {"ccd_count": 0, "os_cores_per_ccd": {}}

    cpu_dirs = glob.glob("/sys/devices/system/cpu/cpu[0-9]*")
    if not cpu_dirs:
        return empty

    # Group logical CPUs by shared L3 slice (= CCD boundary).
    l3_keys: set[str] = set()
    for cpu_dir in cpu_dirs:
        shared = _l3_shared_list(cpu_dir)
        if shared:
            l3_keys.add(shared)

    if not l3_keys:
        return empty

    groups = [sorted(set(_expand_cpu_list(key))) for key in l3_keys]
    groups = [g for g in groups if g]
    if not groups:
        return empty
    groups.sort(key=lambda cpus: cpus[0])

    os_cores_per_ccd: dict[int, int] = {}
    for ccd_idx, ccd_cpus in enumerate(groups):
        seen_phys: set[str] = set()
        for cpu in ccd_cpus:
            sib = (
                _read_sysfs(f"/sys/devices/system/cpu/cpu{cpu}/topology/core_cpus_list")
                or _read_sysfs(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
                or str(cpu)
            )
            seen_phys.add(sib)
        os_cores_per_ccd[ccd_idx] = len(seen_phys)

    return {"ccd_count": len(groups), "os_cores_per_ccd": os_cores_per_ccd}


def _find_first_file(candidates) -> str | None:
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _find_helper() -> str | None:
    return _find_first_file(HELPER_CANDIDATES)


def _find_icon() -> str | None:
    return _find_first_file(ICON_CANDIDATES)


def _find_pkexec() -> str | None:
    for cand in ("/usr/bin/pkexec", "/usr/local/bin/pkexec"):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("pkexec")


class HelperWorker(QThread):
    """Runs the pkexec + root helper call on a background thread so the
    UI never blocks. Emits finished_ok(result_dict) or finished_err(str).

    No separate confirmation dialog precedes this — pkexec's own
    authentication prompt is the confirmation step."""

    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(self, op: str, params: dict, parent=None):
        super().__init__(parent)
        self.op = op
        self.params = params

    def _build_argv(self, pkexec: str, helper: str) -> tuple[list[str], str | None]:
        """Builds the pkexec command line.

        This is the fix for the single most important bug in the old
        version. polkit picks the action to enforce by matching the
        program pkexec is asked to execute against each action's
        org.freedesktop.policykit.exec.path annotation. The old code ran

            pkexec <sys.executable> <helper>

        so the program was /usr/bin/python3, which matches NO action —
        meaning com.ryzencurveoptimizer.set-curve was never selected,
        the custom prompt text never appeared, and auth_admin_keep
        (password caching) never applied. Executing the helper directly
        lets its shebang start the interpreter and makes the annotation
        match.

        If the helper is not executable (running from a git checkout
        that has not been installed), fall back to the interpreter form
        and warn — it still works, just under polkit's generic action.
        """
        if os.access(helper, os.X_OK):
            return [pkexec, helper], None
        return (
            [pkexec, sys.executable, helper],
            f"{helper} is not executable — falling back to the generic polkit "
            "action. Run install.sh so the dedicated policy applies.",
        )

    def run(self):
        pkexec = _find_pkexec()
        if not pkexec:
            self.finished_err.emit("pkexec not found. Is polkit installed?")
            return
        helper = _find_helper()
        if not helper:
            self.finished_err.emit(
                f"{HELPER_NAME} not found.\n"
                f"Expected at: /usr/lib/{APP_ID}/{HELPER_NAME}\n"
                "(or next to this script). Run install.sh."
            )
            return

        argv, warning = self._build_argv(pkexec, helper)
        payload = json.dumps({"op": self.op, "params": self.params})

        try:
            proc = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                text=True,
                timeout=PKEXEC_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.finished_err.emit(
                f"Operation timed out (no response within {PKEXEC_TIMEOUT}s)."
            )
            return
        except OSError as e:
            self.finished_err.emit(f"Failed to run pkexec: {e}")
            return

        if proc.returncode == 126:
            # The polkit prompt was dismissed or authentication failed.
            self.finished_err.emit("Cancelled (authentication dismissed or failed).")
            return
        if proc.returncode == 127:
            self.finished_err.emit("pkexec could not find or start the helper (exit 127).")
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

        if not isinstance(result, dict):
            self.finished_err.emit("Helper returned a non-object JSON response.")
            return

        if warning:
            result["_warning"] = warning
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
        lbl_id.setToolTip(
            f"Fixed SMU slot {slot} of CCD{ccd} (hardware addressing, not the OS core id)"
        )
        lbl_id.setStyleSheet(f"color:{accent}; font-weight:600;")

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("0")
        self.entry.setFixedWidth(44)
        self.entry.setFixedHeight(22)
        self.entry.setAlignment(Qt.AlignCenter)
        self.entry.setValidator(QIntValidator(COPER_MIN, COPER_MAX, self.entry))
        self.entry.setToolTip(f"Curve Optimizer offset for this slot ({COPER_MIN}..{COPER_MAX})")

        self.disable_cb = QCheckBox()
        self.disable_cb.setToolTip(DISABLE_TOOLTIP)
        self.disable_cb.setFixedWidth(18)
        self.disable_cb.toggled.connect(self._on_disable_toggled)

        lay.addWidget(lbl_id)
        lay.addWidget(self.entry)
        lay.addWidget(self.disable_cb)
        lay.addStretch()

    @property
    def label(self) -> str:
        return f"CCD{self.ccd}/S{self.slot}"

    def _on_disable_toggled(self, checked: bool):
        self.entry.setEnabled(not checked)
        if checked:
            self.entry.clear()

    def is_disabled(self) -> bool:
        return self.disable_cb.isChecked()

    def parse(self) -> tuple[str, int | None]:
        """Returns (status, value).

        status is one of: 'disabled', 'empty', 'ok', 'invalid', 'range'.

        The old value() silently clamped out-of-range input and silently
        swallowed unparseable text — so typing 999 quietly applied 20,
        and typing "abc" quietly applied nothing. Both are now reported
        to the caller instead.
        """
        if self.is_disabled():
            return "disabled", None
        txt = self.entry.text().strip()
        if not txt:
            return "empty", None
        try:
            v = int(txt)
        except ValueError:
            return "invalid", None
        if not (COPER_MIN <= v <= COPER_MAX):
            return "range", v
        return "ok", v

    def set_value(self, v):
        if v is None:
            self.entry.clear()
        else:
            self.entry.setText(str(v))

    def set_disabled_state(self, disabled: bool):
        self.disable_cb.setChecked(bool(disabled))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
        self.resize(540, 660)

        icon_path = _find_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self._profiles_ready = True
        try:
            PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Do not take the whole app down just because the config dir
            # is unwritable; only profile save/load is affected.
            self._profiles_ready = False

        layout_info = detect_ccd_layout()
        self.detected_ccds = layout_info["ccd_count"]
        self.ccd_count = self.detected_ccds or CCD_COUNT_FALLBACK
        self.os_cores_per_ccd: dict[int, int] = layout_info["os_cores_per_ccd"]
        self.ccds = list(range(self.ccd_count))
        self.total_slots = self.ccd_count * SLOTS_PER_CCD

        # Any CCD whose OS-visible physical core count isn't the full
        # 8 SMU slots means real-vs-OS core id mapping is ambiguous.
        self.mismatch_ccds = [
            ccd for ccd in self.ccds
            if ccd in self.os_cores_per_ccd and self.os_cores_per_ccd[ccd] != SLOTS_PER_CCD
        ]

        self.core_rows: list[CoreRow] = []
        self._worker: HelperWorker | None = None
        self._ccd_columns: dict[int, QWidget] = {}
        self._ccd_separators: dict[int, QFrame] = {}
        # Authoritative set of active CCDs. The old code re-derived this
        # from widget.isVisible(), which is False for every child before
        # the window is first shown — a latent source of wrong answers.
        self._active_ccds: set[int] = set(self.ccds)
        self._apply_buttons: list[QPushButton] = []

        self._build_ui()
        self._apply_theme()
        self._set_core_mode(all_ccds=True)

        if not self.detected_ccds:
            self._log(
                f"CCD topology could not be read from sysfs — assuming "
                f"{CCD_COUNT_FALLBACK} CCDs. Verify before applying.", "err"
            )
        if not self._profiles_ready:
            self._log(f"Profile directory is not writable: {PROFILES_DIR}", "err")

    # ─────────────────────────────────────────── UI construction ──────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

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
        self.coall_entry.setValidator(QIntValidator(COALL_MIN, COALL_MAX, self.coall_entry))
        self.coall_entry.returnPressed.connect(self._apply_all_core)
        btn_apply_all = QPushButton("Apply All-Core")
        btn_apply_all.setObjectName("btn_accent")
        btn_apply_all.clicked.connect(self._apply_all_core)
        btn_reset = QPushButton("Reset")
        btn_reset.setObjectName("btn_danger")
        btn_reset.setToolTip("Send --set-coall=0, clearing every core's offset.")
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

        self._apply_buttons = [btn_apply_all, btn_reset, btn_apply_cores]

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
                # The separator sits between this column and the NEXT
                # one, so its visibility follows the next CCD.
                self._ccd_separators[self.ccds[col_i + 1]] = sep

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
        # Without a cap the log grows without bound over a long tuning
        # session.
        self.terminal.setMaximumBlockCount(MAX_LOG_BLOCKS)
        term_lay.addWidget(self.terminal)
        root.addWidget(term_box, stretch=1)

        self._log(
            f"{APP_TITLE} {APP_VERSION} started. {self.ccd_count} CCD(s), "
            f"{SLOTS_PER_CCD} fixed SMU slots each ({self.total_slots} total)."
        )
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
            QPushButton:disabled {{ color: {GB_BG4}; border-color: {GB_BG2}; }}

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
        colors = {"info": GB_FG3, "ok": GB_GREEN, "err": GB_RED, "cmd": GB_YELLOW}
        prefix = {"info": "  ", "ok": "OK", "err": "!!", "cmd": ">>"}.get(level, "  ")
        color = colors.get(level, GB_FG3)
        # ryzenadj output and filesystem paths end up here verbatim; any
        # '<' or '&' in them would corrupt or inject into the rich text.
        safe = html.escape(str(msg)).replace("\n", "<br>")
        self.terminal.appendHtml(
            f'<span style="color:{GB_GREY};">[{ts}]</span> '
            f'<span style="color:{color};">{prefix} {safe}</span>'
        )
        # appendHtml does not update the scrollbar range synchronously,
        # so setValue(maximum()) could land short. Moving the cursor is
        # reliable.
        self.terminal.moveCursor(QTextCursor.End)
        self.terminal.ensureCursorVisible()

    # ─────────────────────────────────────────── mode (primary/all) ───
    def _set_core_mode(self, all_ccds: bool):
        if not self.ccds:
            return
        self._active_ccds = set(self.ccds) if all_ccds else {self.ccds[0]}

        for ccd_id, widget in self._ccd_columns.items():
            visible = ccd_id in self._active_ccds
            widget.setVisible(visible)
            sep = self._ccd_separators.get(ccd_id)
            if sep is not None:
                sep.setVisible(visible)

        for row in self.core_rows:
            if row.ccd not in self._active_ccds:
                row.set_disabled_state(False)
                row.set_value(None)

        label = (f"All CCDs ({self.total_slots} slots)" if all_ccds
                 else f"CCD0 Only ({SLOTS_PER_CCD} slots)")
        self._log(f"Core mode: {label}")

    def _active_rows(self) -> list[CoreRow]:
        return [r for r in self.core_rows if r.ccd in self._active_ccds]

    def _clear_core_fields(self):
        for row in self._active_rows():
            row.set_disabled_state(False)
            row.set_value(None)
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
        problems = []
        skipped_disabled = 0

        for row in self._active_rows():
            status, value = row.parse()
            if status == "disabled":
                skipped_disabled += 1
            elif status == "empty":
                continue
            elif status == "invalid":
                problems.append(f"{row.label}: not a number ('{row.entry.text().strip()}')")
            elif status == "range":
                problems.append(f"{row.label}: {value} is outside {COPER_MIN}..{COPER_MAX}")
            else:
                entries.append({
                    "ccd": row.ccd, "ccx": row.ccx, "core": row.slot, "coper": value,
                })

        if problems:
            # Refuse the whole batch. Applying the valid half of a curve
            # and dropping the rest leaves the CPU in a state the user
            # never described.
            QMessageBox.warning(
                self, "Invalid Values",
                "Nothing was applied. Fix these first:\n\n" + "\n".join(problems),
            )
            for p in problems:
                self._log(p, "err")
            return

        if not entries:
            QMessageBox.information(self, "No Input", "No per-core values have been entered.")
            return
        if skipped_disabled:
            self._log(f"Skipping {skipped_disabled} disabled slot(s).")
        self._run_op("set_coper_batch", {"entries": entries})

    def _set_busy(self, busy: bool):
        for btn in self._apply_buttons:
            btn.setEnabled(not busy)

    def _run_op(self, op: str, params: dict):
        if self._worker is not None and self._worker.isRunning():
            self._log("Previous operation still running, please wait.", "err")
            return

        self._log(f"Sending '{op}' via pkexec... (you may be prompted for a password)", "cmd")
        self._set_busy(True)
        worker = HelperWorker(op, params, parent=self)
        worker.finished_ok.connect(self._on_op_ok)
        worker.finished_err.connect(self._on_op_err)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self):
        self._set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

    def _on_op_ok(self, result: dict):
        warning = result.get("_warning")
        if warning:
            self._log(warning, "err")

        if result.get("ok"):
            self._log("Operation applied successfully.", "ok")
        else:
            detail = result.get("error") or result.get("message") or "see per-core results below"
            self._log(f"Operation failed: {detail}", "err")

        for r in result.get("results", []):
            tag = "ok" if r.get("ok") else "err"
            self._log(
                f"CCD{r.get('ccd')}/S{r.get('core')} -> offset {r.get('coper')} "
                f"({r.get('message', '')})",
                tag,
            )
        if not result.get("results") and result.get("message"):
            self._log(result["message"])

    def _on_op_err(self, err: str):
        self._log(err, "err")

    # ─────────────────────────────────────────── profile save/load ────
    def _current_state(self) -> dict:
        cores = []
        for row in self._active_rows():
            status, value = row.parse()
            cores.append({
                "ccd": row.ccd, "ccx": row.ccx, "slot": row.slot,
                "coper": value if status == "ok" else None,
                "disabled": row.is_disabled(),
            })
        # The old code used lstrip("-").isdigit() as an int() guard,
        # which accepts "--5" and then crashes inside int().
        coall_txt = self.coall_entry.text().strip()
        try:
            coall = int(coall_txt) if coall_txt else None
        except ValueError:
            coall = None
        return {
            "version": APP_VERSION,
            "ccd_count": self.ccd_count,
            "coall": coall,
            "cores": cores,
        }

    def _reload_profile_list(self):
        previous = self.profile_combo.currentText()
        self.profile_combo.clear()
        try:
            names = sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
        except OSError:
            names = []
        self.profile_combo.addItems(names)
        if previous:
            idx = self.profile_combo.findText(previous)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

    def _save_profile(self):
        if not self._profiles_ready:
            QMessageBox.critical(self, "Save Error", f"Profile directory is not writable:\n{PROFILES_DIR}")
            return
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if not ok:
            return
        name = name.strip()
        if not PROFILE_NAME_RE.match(name):
            QMessageBox.warning(
                self, "Invalid Name",
                "Use 1-64 characters: letters, digits, space, underscore or "
                "hyphen, starting with a letter or digit.",
            )
            return

        path = PROFILES_DIR / f"{name}.json"
        if path.exists():
            reply = QMessageBox.question(
                self, "Overwrite Profile", f"Profile '{name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        try:
            # Write to a temp file then rename, so an interrupted write
            # cannot leave a truncated profile behind.
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._current_state(), indent=2, ensure_ascii=False))
            os.replace(tmp, path)
        except OSError as e:
            QMessageBox.critical(self, "Save Error", str(e))
            return

        self._reload_profile_list()
        idx = self.profile_combo.findText(name)
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
        if not isinstance(data, dict):
            QMessageBox.critical(self, "Load Error", "Profile file is not a JSON object.")
            return

        coall = data.get("coall")
        self.coall_entry.setText("" if coall is None else str(coall))

        by_pos = {(r.ccd, r.ccx, r.slot): r for r in self.core_rows}
        for row in self.core_rows:
            row.set_disabled_state(False)
            row.set_value(None)

        missing = 0
        for c in data.get("cores", []):
            if not isinstance(c, dict):
                continue
            # Accept both the new "slot" key and the older "core" key.
            slot = c.get("slot", c.get("core", 0))
            row = by_pos.get((c.get("ccd", 0), c.get("ccx", 0), slot))
            if row is None:
                missing += 1
                continue
            disabled = bool(c.get("disabled", False))
            row.set_disabled_state(disabled)
            if not disabled:
                row.set_value(c.get("coper"))

        self._log(f"Profile loaded: {name}")
        if missing:
            self._log(
                f"{missing} slot(s) in the profile do not exist on this "
                f"topology ({self.ccd_count} CCD(s)) and were ignored.", "err"
            )

    def _delete_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        reply = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            (PROFILES_DIR / f"{name}.json").unlink(missing_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Delete Error", str(e))
            return
        self._reload_profile_list()
        self._log(f"Profile deleted: {name}")

    # ─────────────────────────────────────────── shutdown ─────────────
    def closeEvent(self, event):
        """Qt prints 'QThread: Destroyed while thread is still running'
        and may abort if the window closes mid-operation."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            reply = QMessageBox.question(
                self, "Operation In Progress",
                "An operation is still running. Wait for it to finish?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                event.ignore()
                return
            worker.wait(PKEXEC_TIMEOUT * 1000)
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=APP_TITLE)
    parser.add_argument("-V", "--version", action="version",
                        version=f"{APP_TITLE} {APP_VERSION}")
    parser.add_argument("--detect", action="store_true",
                        help="print the detected CCD topology and exit (no GUI)")
    args = parser.parse_args()

    if args.detect:
        info = detect_ccd_layout()
        print(json.dumps(info, indent=2))
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    # Lets Wayland compositors match the window to its .desktop entry,
    # which is what makes the taskbar icon and app name show correctly.
    app.setDesktopFileName(APP_ID)

    icon_path = _find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
ryzen_curve_optimizer_helper.py — root helper invoked via pkexec by the
Ryzen Curve Optimizer applet.

Small and standalone, with exactly one job: read a JSON command from
stdin, validate it, run the `ryzenadj` binary with ONLY fixed/whitelisted
arguments, and write the result back to stdout as JSON.

Security principles:
  * shell=True is NEVER used — ryzenadj is always invoked with an
    argument list.
  * The ryzenadj binary path is resolved from a fixed candidate list,
    never from the caller's PATH/environment, and the resolved binary is
    rejected unless it is owned by root and not writable by group/other
    (executing a user-writable binary as root would be a trivial
    privilege escalation).
  * The child process gets a minimal, hard-coded environment.
  * Every numeric value must parse as an int AND fall inside a strict
    range — out-of-range or non-numeric input is REJECTED, never
    silently coerced to a default (a silent default would apply a
    voltage offset the user never asked for).
  * stdin size is bounded.
  * No file is ever written or read here; this helper only calls
    ryzenadj.

This script is the direct pkexec target: the polkit action's
org.freedesktop.policykit.exec.path annotation must point at this exact
installed path, and the file must be root-owned and mode 0755.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

MAX_STDIN_BYTES = 64 * 1024
RYZENADJ_TIMEOUT = 10
MAX_ENTRIES = 64

# Trusted fixed candidates — the caller's $PATH is never consulted.
RYZENADJ_CANDIDATES = (
    "/usr/bin/ryzenadj",
    "/usr/sbin/ryzenadj",
    "/usr/local/bin/ryzenadj",
    "/usr/local/sbin/ryzenadj",
    "/opt/ryzenadj/ryzenadj",
)

# Minimal environment for the child. pkexec already sanitises the
# environment, but being explicit costs nothing.
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
}

# ryzenadj's --set-coper encoding packs ccd/ccx/core into 4-bit fields,
# so each is always 0-15 regardless of the Zen topology the GUI detects.
FIELD_MAX = 0xF

# Curve Optimizer offset bounds. Positive offsets raise voltage and are
# deliberately capped low; negative offsets are the undervolt direction.
CO_MIN, CO_MAX = -50, 20


class ValidationError(ValueError):
    """Raised when a client-supplied value fails validation."""


def _require_int(value, name: str, lo: int, hi: int) -> int:
    """Parses `value` as an int and enforces lo <= v <= hi.

    Rejects rather than clamps: a clamped value is a value the user did
    not ask for, and silently applying it to a voltage curve is worse
    than failing loudly. bool is refused explicitly because bool is a
    subclass of int in Python and True would otherwise become 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer, got {type(value).__name__}")
    if not (lo <= value <= hi):
        raise ValidationError(f"{name} out of range: {value} (allowed {lo}..{hi})")
    return value


def _is_safe_executable(path: str) -> bool:
    """True if `path` is a regular file, executable, owned by root, and
    not writable by group or other."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    if st.st_uid != 0:
        return False
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    return os.access(path, os.X_OK)


def _find_ryzenadj() -> str | None:
    for cand in RYZENADJ_CANDIDATES:
        if _is_safe_executable(cand):
            return cand
    return None


def _run(args: list[str]) -> tuple[bool, str]:
    ryzenadj = _find_ryzenadj()
    if not ryzenadj:
        return False, (
            "no usable ryzenadj binary found (searched: "
            + ", ".join(RYZENADJ_CANDIDATES)
            + "). It must exist, be root-owned and not group/world-writable."
        )
    try:
        result = subprocess.run(
            [ryzenadj, *args],
            capture_output=True,
            text=True,
            timeout=RYZENADJ_TIMEOUT,
            env=SAFE_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"ryzenadj timed out after {RYZENADJ_TIMEOUT}s"
    except OSError as e:
        return False, f"failed to execute ryzenadj: {e}"

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return False, err or out or f"ryzenadj exited with code {result.returncode}"
    return True, out or "OK"


def _encode_coper(ccd: int, ccx: int, core: int, coper: int) -> int:
    """ryzenadj per-core CO encoding:
    ((ccd << 4 | ccx) << 4 | core) << 20 | (coper & 0xFFFF)

    The offset is carried as a 16-bit two's-complement value, which is
    why negatives are masked rather than passed through.
    """
    return (((ccd << 4 | ccx) << 4 | core) << 20) | (coper & 0xFFFF)


def op_set_coall(params: dict) -> dict:
    """Applies a single Curve Optimizer offset to every core."""
    value = _require_int(params.get("value"), "value", CO_MIN, CO_MAX)
    ok, msg = _run([f"--set-coall={value}"])
    return {"ok": ok, "message": msg, "applied": {"coall": value}}


def op_set_coper_batch(params: dict) -> dict:
    """Applies per-core Curve Optimizer offsets.

    `entries`: [{ccd, ccx, core, coper}, ...]

    Every entry is validated up front — if ANY entry is malformed the
    whole batch is rejected before a single ryzenadj call is made, so
    the CPU is never left with a half-applied curve because of a typo
    in the payload. Execution failures (ryzenadj itself erroring on one
    core) are still reported per entry.
    """
    entries = params.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValidationError("entries must be a non-empty list")
    if len(entries) > MAX_ENTRIES:
        raise ValidationError(f"too many entries: {len(entries)} (max {MAX_ENTRIES})")

    validated = []
    seen = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise ValidationError(f"entry[{i}] must be an object")
        ccd = _require_int(e.get("ccd"), f"entry[{i}].ccd", 0, FIELD_MAX)
        ccx = _require_int(e.get("ccx"), f"entry[{i}].ccx", 0, FIELD_MAX)
        core = _require_int(e.get("core"), f"entry[{i}].core", 0, FIELD_MAX)
        coper = _require_int(e.get("coper"), f"entry[{i}].coper", CO_MIN, CO_MAX)
        key = (ccd, ccx, core)
        if key in seen:
            raise ValidationError(f"duplicate slot in batch: ccd={ccd} ccx={ccx} core={core}")
        seen.add(key)
        validated.append((ccd, ccx, core, coper))

    results = []
    all_ok = True
    for ccd, ccx, core, coper in validated:
        ok, msg = _run([f"--set-coper={_encode_coper(ccd, ccx, core, coper)}"])
        if not ok:
            all_ok = False
        results.append({
            "ok": ok, "message": msg,
            "ccd": ccd, "ccx": ccx, "core": core, "coper": coper,
        })

    return {"ok": all_ok, "results": results}


def op_reset(params: dict) -> dict:
    """Resets the Curve Optimizer to stock (coall=0 clears every core)."""
    ok, msg = _run(["--set-coall=0"])
    return {"ok": ok, "message": msg}


OPERATIONS = {
    "set_coall": op_set_coall,
    "set_coper_batch": op_set_coper_batch,
    "reset": op_reset,
}


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        _emit({"ok": False, "error": "payload too large"})
        return 1

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _emit({"ok": False, "error": f"invalid JSON: {e}"})
        return 1

    if not isinstance(payload, dict):
        _emit({"ok": False, "error": "payload must be a JSON object"})
        return 1

    op_name = payload.get("op")
    handler = OPERATIONS.get(op_name)
    if handler is None:
        _emit({"ok": False, "error": f"unknown op: {op_name!r}"})
        return 1

    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        _emit({"ok": False, "error": "params must be a JSON object"})
        return 1

    try:
        result = handler(params)
    except ValidationError as e:
        result = {"ok": False, "error": f"invalid request: {e}"}
    except Exception as e:  # noqa: BLE001 — last-resort guard, must stay JSON
        result = {"ok": False, "error": f"internal error: {type(e).__name__}: {e}"}

    _emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

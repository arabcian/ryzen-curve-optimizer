#!/usr/bin/env python3
"""
ryzen_curve_optimizer_helper.py — root helper script invoked via pkexec
by the Ryzen Curve Optimizer applet.

Small and standalone, with exactly one job: read a JSON command from
stdin, validate it, run the `ryzenadj` binary with ONLY fixed/whitelisted
arguments, and write the result back to stdout as JSON.

Security principles (same pattern as m16R1-power-manager/root_helper.py):
  * shell=True is NEVER used — ryzenadj is always invoked with an
    argument list.
  * The ryzenadj binary path is resolved from a fixed candidate list,
    never from the caller's PATH/environment (the caller's environment
    is never trusted in a root context).
  * Every numeric value is coerced with int() and clamped to a strict
    range — the client can never inject a string/free-form argument.
  * stdin size is bounded.
  * No file is ever written or read here; this helper only calls
    ryzenadj.
"""

from __future__ import annotations

import json
import subprocess
import sys

MAX_STDIN_BYTES = 64 * 1024

# Trusted fixed candidates in a root context — the user's $PATH is
# never trusted here.
RYZENADJ_CANDIDATES = [
    "/usr/bin/ryzenadj",
    "/usr/local/bin/ryzenadj",
    "/usr/sbin/ryzenadj",
]

# ryzenadj's --set-coper encoding scheme packs the ccd/ccx/core fields
# into 4 bits each (see the encoded calculation below), so the valid
# range is always 0-15 — this stays correct for ANY Zen topology the
# GUI detects live (different CCD counts, different core counts per
# CCD), rather than assuming a fixed layout specific to one chip. This
# is the actual bound enforced here; values sent by the GUI are never
# trusted as-is.
CCD_CCX_CORE_FIELD_MAX = 0xF  # 4-bit field width


def _find_ryzenadj() -> str | None:
    import os
    for cand in RYZENADJ_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _run(args: list[str]) -> tuple[bool, str]:
    ryzenadj = _find_ryzenadj()
    if not ryzenadj:
        return False, "ryzenadj binary not found on this system"
    try:
        result = subprocess.run(
            [ryzenadj, *args], capture_output=True, text=True, timeout=10
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            return False, err or out or f"exit code {result.returncode}"
        return True, out or "OK"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _clamp(v, lo, hi, default=0) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def op_set_coall(params: dict) -> dict:
    """Applies a single Curve Optimizer offset to every core."""
    value = _clamp(params.get("value"), -50, 20)
    ok, msg = _run([f"--set-coall={value}"])
    return {"ok": ok, "message": msg, "applied": {"coall": value}}


def op_set_coper_batch(params: dict) -> dict:
    """Applies a per-core Curve Optimizer offset to one or more cores
    (called once by the GUI's "Apply Per-Core" button, with every core
    currently filled in). `entries`: [{ccd, ccx, core, coper}, ...]

    Each entry is validated independently and applied one at a time via
    --set-coper=; a failure on one entry does not block the others
    (each result is reported individually)."""
    entries = params.get("entries")
    if not isinstance(entries, list) or not entries:
        return {"ok": False, "error": "entries must be a non-empty list"}
    if len(entries) > 64:
        return {"ok": False, "error": "too many entries (max 64 cores)"}

    results = []
    all_ok = True
    for e in entries:
        if not isinstance(e, dict):
            all_ok = False
            results.append({"ok": False, "error": "entry must be an object"})
            continue
        ccd = _clamp(e.get("ccd"), 0, CCD_CCX_CORE_FIELD_MAX)
        ccx = _clamp(e.get("ccx"), 0, CCD_CCX_CORE_FIELD_MAX)
        core = _clamp(e.get("core"), 0, CCD_CCX_CORE_FIELD_MAX)
        coper = _clamp(e.get("coper"), -50, 20)

        # ryzenadj encoding scheme: ((ccd<<4|ccx)<<4|core)<<20 | (coper & 0xFFFF)
        encoded = (((ccd << 4 | ccx) << 4 | core) << 20) | (coper & 0xFFFF)
        ok, msg = _run([f"--set-coper={encoded}"])
        if not ok:
            all_ok = False
        results.append({
            "ok": ok, "message": msg,
            "ccd": ccd, "ccx": ccx, "core": core, "coper": coper,
        })

    return {"ok": all_ok, "results": results}


def op_reset(params: dict) -> dict:
    """Resets the Curve Optimizer (coall=0)."""
    ok, msg = _run(["--set-coall=0"])
    return {"ok": ok, "message": msg}


OPERATIONS = {
    "set_coall": op_set_coall,
    "set_coper_batch": op_set_coper_batch,
    "reset": op_reset,
}


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        print(json.dumps({"ok": False, "error": "payload too large"}))
        return 1

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"invalid JSON: {e}"}))
        return 1

    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "payload must be a JSON object"}))
        return 1

    op_name = payload.get("op")
    handler = OPERATIONS.get(op_name)
    if not handler:
        print(json.dumps({"ok": False, "error": f"unknown op: {op_name!r}"}))
        return 1

    try:
        result = handler(payload.get("params") or {})
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"internal error: {e}"}

    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

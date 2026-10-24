#!/bin/bash
# uninstall.sh — Ryzen Curve Optimizer uninstaller
#
# Usage:
#   sudo ./uninstall.sh                     # keeps saved profiles and settings
#   sudo ./uninstall.sh --purge             # also deletes every user's profiles/settings
#   sudo PREFIX=/usr/local ./uninstall.sh   # match a non-default install
#
# Removes everything install.sh creates, plus any leftovers from the earlier
# Python/ryzenadj version (including its older /usr/local-only layout).

set -euo pipefail

APP_ID="ryzen-curve-optimizer"
HELPER_NAME="ryzen-curve-optimizer-helper"
LEGACY_HELPER="ryzen_curve_optimizer_helper.py"
POLICY_NAME="com.ryzencurveoptimizer.policy"
RULES_NAME="49-ryzen-curve-optimizer.rules"

PREFIX="${PREFIX:-/usr}"
DESTDIR="${DESTDIR:-}"
POLKIT_ACTION_DIR="/usr/share/polkit-1/actions"
POLKIT_RULESDIR="${POLKIT_RULESDIR:-/etc/polkit-1/rules.d}"

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root: sudo ./uninstall.sh" >&2
    exit 1
fi

info() { echo "-> $*"; }

# Files/dirs from the current layout, plus the legacy paths of the Python version.
FILES=(
    "$DESTDIR$PREFIX/bin/$APP_ID"
    "$DESTDIR$PREFIX/share/applications/$APP_ID.desktop"
    "$DESTDIR$PREFIX/share/icons/hicolor/128x128/apps/$APP_ID.png"
    "$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
    "$DESTDIR$POLKIT_ACTION_DIR/$POLICY_NAME"
    "$DESTDIR$POLKIT_RULESDIR/$RULES_NAME"
    "$DESTDIR/usr/local/bin/$LEGACY_HELPER"
)
DIRS=(
    "$DESTDIR$PREFIX/lib/$APP_ID"
    "$DESTDIR$PREFIX/share/$APP_ID"
    "$DESTDIR/usr/local/lib/$APP_ID"
    "$DESTDIR/usr/local/share/$APP_ID"
)

for f in "${FILES[@]}"; do
    if [ -e "$f" ] || [ -L "$f" ]; then
        info "Removing $f"
        rm -f "$f"
    fi
done

for d in "${DIRS[@]}"; do
    if [ -d "$d" ]; then
        info "Removing $d"
        rm -rf "$d"
    fi
done

if [ -z "$DESTDIR" ]; then
    rm -rf "/run/$APP_ID"   # tmpfs: SMN lock file only
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
fi

# ── Saved profiles and settings ───────────────────────────────────────
if [ "$PURGE" -eq 1 ]; then
    # They live under each user's own $XDG_CONFIG_HOME (or ~/.config as a
    # fallback), so there is no single system-wide path — walk every real
    # user's home instead. Read the whole file first: the loop body must not
    # consume stdin from /etc/passwd.
    info "Purging saved profiles and settings for all users..."
    while IFS=: read -r _ _ uid _ _ home _; do
        # A non-numeric uid field would abort the script under `set -e`.
        case "$uid" in ''|*[!0-9]*) continue ;; esac
        if [ "$uid" -ge 1000 ] || [ "$uid" -eq 0 ]; then
            [ -n "$home" ] && [ -d "$home" ] && [ ! -L "$home" ] || continue
            profile_dir="$home/.config/$APP_ID"
            # Root is about to rm -rf inside a home that belongs to someone else: a symlinked
            # ~/.config (planted by that user) would point it anywhere. Only the real path
            # directly under the real home is removed.
            real_home="$(realpath -e -- "$home")" || continue
            [ -d "$profile_dir" ] && [ ! -L "$profile_dir" ] \
                && [ "$(realpath -e -- "$profile_dir")" = "$real_home/.config/$APP_ID" ] || continue
            echo "   removing $profile_dir"
            rm -rf -- "$profile_dir"
        fi
    done < <(cat /etc/passwd)
else
    echo "-> Saved profiles and settings were left in place (~/.config/$APP_ID/)."
    echo "   Re-run with --purge to remove them too."
fi

info "Done."

#!/bin/bash
# install.sh — Ryzen Curve Optimizer installer
#
# Usage:
#   sudo ./install.sh                     # installs under /usr
#   sudo PREFIX=/usr/local ./install.sh   # installs under /usr/local
#   DESTDIR=/tmp/stage ./install.sh       # staged install (packaging)
#
# Installed layout (PREFIX=/usr):
#   /usr/bin/ryzen-curve-optimizer                              (0755 root:root)
#   /usr/lib/ryzen-curve-optimizer/ryzen_curve_optimizer_helper.py (0755 root:root)
#   /usr/share/ryzen-curve-optimizer/icon.png
#   /usr/share/applications/ryzen-curve-optimizer.desktop
#   /usr/share/icons/hicolor/128x128/apps/ryzen-curve-optimizer.png
#   /usr/share/icons/hicolor/scalable/apps/ryzen-curve-optimizer.svg
#   /usr/share/polkit-1/actions/com.ryzencurveoptimizer.policy
#
# NOTE for Gentoo/portage users: /usr is package-manager territory. This
# installs files portage does not know about. Use PREFIX=/usr/local, or
# wrap this in an ebuild, if you want to keep /usr clean.

set -euo pipefail

APP_ID="ryzen-curve-optimizer"
HELPER_NAME="ryzen_curve_optimizer_helper.py"
POLICY_NAME="com.ryzencurveoptimizer.policy"

PREFIX="${PREFIX:-/usr}"
DESTDIR="${DESTDIR:-}"

# polkit only reads its action files from /usr/share/polkit-1/actions —
# the path is compiled in, so it does NOT follow PREFIX.
POLKIT_ACTION_DIR="/usr/share/polkit-1/actions"

BIN_DIR="$DESTDIR$PREFIX/bin"
LIB_DIR="$DESTDIR$PREFIX/lib/$APP_ID"
SHARE_DIR="$DESTDIR$PREFIX/share/$APP_ID"
APPS_DIR="$DESTDIR$PREFIX/share/applications"
ICON_DIR="$DESTDIR$PREFIX/share/icons/hicolor"
POLICY_DIR="$DESTDIR$POLKIT_ACTION_DIR"

# Path the polkit annotation must point at, as seen by the RUNNING
# system (never DESTDIR-prefixed).
HELPER_RUNTIME_PATH="$PREFIX/lib/$APP_ID/$HELPER_NAME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root: sudo ./install.sh" >&2
    exit 1
fi

die() { echo "error: $*" >&2; exit 1; }
info() { echo "-> $*"; }
warn() { echo "   warning: $*" >&2; }

for f in ryzen_curve_optimizer.py "$HELPER_NAME" "$POLICY_NAME" "$APP_ID.desktop"; do
    [ -f "$SCRIPT_DIR/$f" ] || die "missing source file: $f"
done

# ── Remove a previous install from the old hard-coded /usr/local paths ─
OLD_LIB="/usr/local/lib/$APP_ID"
OLD_SHARE="/usr/local/share/$APP_ID"
if [ -z "$DESTDIR" ] && { [ -d "$OLD_LIB" ] || [ -d "$OLD_SHARE" ]; }; then
    if [ "$PREFIX/lib/$APP_ID" != "$OLD_LIB" ]; then
        info "Removing previous install from $OLD_LIB / $OLD_SHARE"
        rm -rf "$OLD_LIB" "$OLD_SHARE"
    fi
fi

# ── Main application ──────────────────────────────────────────────────
info "Installing application: $PREFIX/bin/$APP_ID"
install -d -m 0755 "$BIN_DIR"
install -m 0755 "$SCRIPT_DIR/ryzen_curve_optimizer.py" "$BIN_DIR/$APP_ID"

# ── Root helper ───────────────────────────────────────────────────────
# Mode 0755 root:root is mandatory: pkexec refuses to execute a program
# that is group- or world-writable, or not owned by root.
info "Installing root helper: $HELPER_RUNTIME_PATH"
install -d -m 0755 "$LIB_DIR"
install -m 0755 "$SCRIPT_DIR/$HELPER_NAME" "$LIB_DIR/$HELPER_NAME"
if [ -z "$DESTDIR" ]; then
    chown root:root "$BIN_DIR/$APP_ID" "$LIB_DIR/$HELPER_NAME"
fi

# ── Icons ─────────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/icon.png" ]; then
    info "Installing icon"
    install -d -m 0755 "$SHARE_DIR" "$ICON_DIR/128x128/apps"
    install -m 0644 "$SCRIPT_DIR/icon.png" "$SHARE_DIR/icon.png"
    install -m 0644 "$SCRIPT_DIR/icon.png" "$ICON_DIR/128x128/apps/$APP_ID.png"
fi
if [ -f "$SCRIPT_DIR/icon.svg" ]; then
    install -d -m 0755 "$ICON_DIR/scalable/apps"
    install -m 0644 "$SCRIPT_DIR/icon.svg" "$ICON_DIR/scalable/apps/$APP_ID.svg"
fi

# ── Desktop entry ─────────────────────────────────────────────────────
info "Installing desktop entry: $PREFIX/share/applications/$APP_ID.desktop"
install -d -m 0755 "$APPS_DIR"
install -m 0644 "$SCRIPT_DIR/$APP_ID.desktop" "$APPS_DIR/$APP_ID.desktop"

# ── Polkit policy ─────────────────────────────────────────────────────
info "Installing polkit policy: $POLKIT_ACTION_DIR/$POLICY_NAME"
install -d -m 0755 "$POLICY_DIR"
install -m 0644 "$SCRIPT_DIR/$POLICY_NAME" "$POLICY_DIR/$POLICY_NAME"

# The annotation ships with the /usr default; rewrite it if the helper
# actually landed somewhere else. If this path and the pkexec argv ever
# disagree, the action silently stops being enforced.
sed -i \
    "s#<annotate key=\"org.freedesktop.policykit.exec.path\">[^<]*</annotate>#<annotate key=\"org.freedesktop.policykit.exec.path\">$HELPER_RUNTIME_PATH</annotate>#" \
    "$POLICY_DIR/$POLICY_NAME"

if grep -qF "$HELPER_RUNTIME_PATH" "$POLICY_DIR/$POLICY_NAME"; then
    info "Policy exec.path set to $HELPER_RUNTIME_PATH"
else
    die "failed to set exec.path in the installed policy file"
fi

# ── Refresh desktop/icon caches ───────────────────────────────────────
if [ -z "$DESTDIR" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
fi

# ── Runtime dependency report ─────────────────────────────────────────
echo
info "Checking runtime dependencies"
missing=0

if command -v python3 >/dev/null 2>&1; then
    echo "   [ok]   python3: $(command -v python3)"
else
    echo "   [MISS] python3 not found"; missing=1
fi

if python3 -c 'import PySide6' >/dev/null 2>&1; then
    echo "   [ok]   PySide6"
else
    echo "   [MISS] PySide6 (emerge dev-python/pyside6, or pip install --user PySide6)"; missing=1
fi

if command -v pkexec >/dev/null 2>&1; then
    echo "   [ok]   pkexec: $(command -v pkexec)"
else
    echo "   [MISS] pkexec (install polkit)"; missing=1
fi

ryzenadj_found=""
for cand in /usr/bin/ryzenadj /usr/sbin/ryzenadj /usr/local/bin/ryzenadj \
            /usr/local/sbin/ryzenadj /opt/ryzenadj/ryzenadj; do
    if [ -x "$cand" ]; then ryzenadj_found="$cand"; break; fi
done
if [ -n "$ryzenadj_found" ]; then
    echo "   [ok]   ryzenadj: $ryzenadj_found"
    # The helper runs as root and refuses a binary anyone else can edit.
    owner="$(stat -c '%u' "$ryzenadj_found")"
    perms="$(stat -c '%a' "$ryzenadj_found")"
    if [ "$owner" != "0" ]; then
        warn "$ryzenadj_found is not owned by root — the helper will refuse it"
    fi
    case "$perms" in
        *[2367]|*[2367]?) warn "$ryzenadj_found is group/world-writable ($perms) — the helper will refuse it" ;;
    esac
else
    echo "   [MISS] ryzenadj not found in any trusted location"
    echo "          (the helper only looks at /usr/bin, /usr/sbin, /usr/local/bin,"
    echo "           /usr/local/sbin, /opt/ryzenadj — never at \$PATH)"
    missing=1
fi

echo
if [ "$missing" -ne 0 ]; then
    echo "Installation finished, but some dependencies are missing (see above)."
else
    echo "Installation finished."
fi
echo
echo "Run it from a terminal with:  $APP_ID"
echo "...or launch 'Ryzen Curve Optimizer' from your application menu."
echo "It runs unprivileged; pkexec prompts only when you apply a change."

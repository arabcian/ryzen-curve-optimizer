#!/bin/bash
# install.sh — Ryzen Curve Optimizer installer (Rust root helper + Qt6 GUI)
#
# Usage:
#   sudo ./install.sh                       build + install under /usr
#   sudo PREFIX=/usr/local ./install.sh     install under /usr/local
#   DESTDIR=/tmp/stage ./install.sh         staged install (packaging)
#   sudo ./install.sh --no-build            install what is already built
#   sudo ./install.sh --security-level=N    1 or 2 (see below)
#
# Security level (polkit, who may apply offsets without a password):
#   1  a wheel/sudo member at the machine (local, active session) is silent.
#   2  every apply asks for the administrator password (polkit caches it
#      briefly). Default; it is also what the old Python applet did.
#   Everyone else (SSH, other seats) is always asked. Asked interactively when
#   not given and nothing is installed yet; RCO_SECURITY_LEVEL works too.
#   An upgrade keeps the level of the installed rules file.
#
# Installed layout (PREFIX=/usr):
#   /usr/bin/ryzen-curve-optimizer                                     GUI (0755 root:root)
#   /usr/lib/ryzen-curve-optimizer/ryzen-curve-optimizer-helper        root helper (0755 root:root)
#   /usr/share/ryzen-curve-optimizer/icon.png
#   /usr/share/applications/ryzen-curve-optimizer.desktop
#   /usr/share/icons/hicolor/128x128/apps/ryzen-curve-optimizer.png
#   /usr/share/icons/hicolor/scalable/apps/ryzen-curve-optimizer.svg
#   /usr/share/polkit-1/actions/com.ryzencurveoptimizer.policy
#   /etc/polkit-1/rules.d/49-ryzen-curve-optimizer.rules               (POLKIT_RULESDIR)
#
# An earlier install of the Python/ryzenadj version is detected and migrated:
# its files are removed once the new ones are in place and verified, the polkit
# action is replaced (same id, new helper), and saved profiles are kept (same
# directory, same format). See migrate_legacy below.
#
# NOTE for Gentoo/portage users: /usr is package-manager territory. This
# installs files portage does not know about. Use PREFIX=/usr/local, or
# wrap this in an ebuild, if you want to keep /usr clean.

set -euo pipefail

APP_ID="ryzen-curve-optimizer"
HELPER_NAME="ryzen-curve-optimizer-helper"
POLICY_NAME="com.ryzencurveoptimizer.policy"
ACTION_ID="com.ryzencurveoptimizer.set-curve"
RULES_NAME="49-ryzen-curve-optimizer.rules"
LEGACY_HELPER="ryzen_curve_optimizer_helper.py"

PREFIX="${PREFIX:-/usr}"
DESTDIR="${DESTDIR:-}"
SECLEVEL="${RCO_SECURITY_LEVEL:-}"
BUILD=1 HARDEN=1
for a in "$@"; do
    case "$a" in
        --no-build) BUILD=0 ;;
        --no-harden) HARDEN=0 ;;
        --security-level=*) SECLEVEL="${a#*=}" ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

# polkit only reads its action files from /usr/share/polkit-1/actions —
# the path is compiled in, so it does NOT follow PREFIX.
POLKIT_ACTION_DIR="/usr/share/polkit-1/actions"
POLKIT_RULESDIR="${POLKIT_RULESDIR:-/etc/polkit-1/rules.d}"

BIN_DIR="$DESTDIR$PREFIX/bin"
LIB_DIR="$DESTDIR$PREFIX/lib/$APP_ID"
SHARE_DIR="$DESTDIR$PREFIX/share/$APP_ID"
APPS_DIR="$DESTDIR$PREFIX/share/applications"
ICON_DIR="$DESTDIR$PREFIX/share/icons/hicolor"
POLICY_DIR="$DESTDIR$POLKIT_ACTION_DIR"
RULES_DIR="$DESTDIR$POLKIT_RULESDIR"

# Path the polkit annotation and the GUI must point at, as seen by the RUNNING
# system (never DESTDIR-prefixed).
HELPER_RUNTIME_PATH="$PREFIX/lib/$APP_ID/$HELPER_NAME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "-> $*"; }
warn() { echo "   warning: $*" >&2; }

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root: sudo ./install.sh" >&2
    exit 1
fi

for f in Cargo.toml Cargo.lock gui/CMakeLists.txt icon.png icon.svg "$POLICY_NAME" "$RULES_NAME" "$APP_ID.desktop"; do
    [ -f "$f" ] || die "missing source file: $f"
done

# ── Security level ────────────────────────────────────────────────────
# Upgrade: keep the level of the rules file being replaced (no prompt, no silent reset).
OLDRULES="$RULES_DIR/$RULES_NAME"
if [ -z "$SECLEVEL" ] && [ -f "$OLDRULES" ]; then
    SECLEVEL="$(sed -n 's/^var SECURITY_LEVEL = \([12]\);.*/\1/p' "$OLDRULES" | head -n1)"
    [ -n "$SECLEVEL" ] && info "Keeping polkit security level $SECLEVEL from the installed rules."
fi
if [ -z "$SECLEVEL" ]; then
    SECLEVEL=2
    if [ -z "$DESTDIR" ] && [ -t 0 ]; then
        echo "Polkit security level for the root helper:"
        echo "  1) password-free for an administrator (wheel or sudo) at the machine"
        echo "  2) every apply asks for the administrator password (polkit caches it briefly)  [default]"
        ans=""; read -r -p "Level [2]: " ans || true
        [ -n "$ans" ] && SECLEVEL="$ans"
    fi
fi
case "$SECLEVEL" in 1|2) ;; *) die "invalid security level '$SECLEVEL' (use 1 or 2)" ;; esac

as_user() { if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then sudo -u "$SUDO_USER" "$@"; else "$@"; fi; }

# ── Build ─────────────────────────────────────────────────────────────
BUILT_HELPER="target/release/$HELPER_NAME"
BUILT_GUI="gui/build/$APP_ID"
if [ "$BUILD" -eq 1 ]; then
    for t in cmake g++; do command -v "$t" >/dev/null 2>&1 || die "$t not found (needed to build the GUI)"; done
    # sudo's secure_path drops the user's PATH (rustup installs into ~/.cargo/bin): ask the user's shell.
    CARGO="$(as_user bash -c 'command -v cargo || { [ -x "$HOME/.cargo/bin/cargo" ] && echo "$HOME/.cargo/bin/cargo"; }' || true)"
    [ -n "$CARGO" ] || die "cargo not found (needed to build the root helper): install Rust, or use --no-build with a prebuilt tree"
    info "Building the root helper (Rust)"
    as_user "$CARGO" build --release --locked
    info "Building the GUI (Qt6)"
    as_user cmake -S gui -B gui/build -DCMAKE_BUILD_TYPE=Release \
        -DRCO_HELPER_DIR="$PREFIX/lib/$APP_ID" -DRCO_HARDEN="$([ "$HARDEN" -eq 1 ] && echo ON || echo OFF)"
    as_user cmake --build gui/build -j"$(nproc)"
fi
[ -x "$BUILT_HELPER" ] || die "$BUILT_HELPER not built (run without --no-build)"
[ -x "$BUILT_GUI" ]    || die "$BUILT_GUI not built (run without --no-build)"
# The GUI pins the helper path at compile time; it must be the path polkit pins.
if ! as_user "$BUILT_GUI" --version 2>/dev/null | grep -qF "(helper: $PREFIX/lib/$APP_ID)"; then
    die "$BUILT_GUI was built for a different PREFIX; rebuild without --no-build (or with PREFIX matching the build)"
fi

# ── Legacy detection (read-only) ──────────────────────────────────────
# Old Python applet: a python script at <base>/bin/$APP_ID, the helper
# <base>/lib/$APP_ID/$LEGACY_HELPER (or, in the very first layout,
# <base>/bin/$LEGACY_HELPER), and a polkit action whose exec.path ends in .py.
# <base> is /usr, /usr/local and the current PREFIX.
is_python_script() {
    [ -f "$1" ] && [ ! -L "$1" ] && [ "$(head -c2 "$1" 2>/dev/null)" = '#!' ] && head -n1 "$1" | grep -qi python
}
LEGACY_BASES=()
for b in /usr /usr/local "$PREFIX"; do
    case " ${LEGACY_BASES[*]:-} " in *" $b "*) ;; *) LEGACY_BASES+=("$b") ;; esac
done
LEGACY_FILES=()
if [ -z "$DESTDIR" ]; then
    for b in "${LEGACY_BASES[@]}"; do
        is_python_script "$b/bin/$APP_ID"                  && LEGACY_FILES+=("$b/bin/$APP_ID")
        [ -f "$b/lib/$APP_ID/$LEGACY_HELPER" ]             && LEGACY_FILES+=("$b/lib/$APP_ID/$LEGACY_HELPER")
        [ -f "$b/bin/$LEGACY_HELPER" ]                     && LEGACY_FILES+=("$b/bin/$LEGACY_HELPER")
    done
    if [ -f "$POLKIT_ACTION_DIR/$POLICY_NAME" ] && grep -q 'exec.path">[^<]*\.py<' "$POLKIT_ACTION_DIR/$POLICY_NAME"; then
        LEGACY_FILES+=("$POLKIT_ACTION_DIR/$POLICY_NAME")
    fi
fi

# The old tool drove the CPU through ryzenadj, which knows more CPUs than the native SMU
# interface of this version. If the old install works on a CPU this one cannot drive, stop
# before anything is touched rather than replace a working tool with an unusable one.
if [ "${#LEGACY_FILES[@]}" -gt 0 ]; then
    probe_dir="$(mktemp -d)"
    trap 'rm -rf "$probe_dir"' EXIT
    install -m 0755 "$BUILT_HELPER" "$probe_dir/helper"   # run a root-owned copy, not the build tree
    probe="$(echo '{"op":"probe"}' | "$probe_dir/helper" || true)"
    if grep -q '"unsupported_cpu":true' <<<"$probe"; then
        echo "An earlier Ryzen Curve Optimizer install was found, but this version cannot drive this CPU:" >&2
        echo "  $probe" >&2
        echo "Nothing was changed. Keep the old install (it uses ryzenadj), or see README.md → Supported CPUs." >&2
        exit 1
    fi
fi

# ── Install ───────────────────────────────────────────────────────────
own=(-o root -g root); [ -z "$DESTDIR" ] || own=()

info "Installing application: $PREFIX/bin/$APP_ID"
install -d "${own[@]}" -m 0755 "$BIN_DIR"
install "${own[@]}" -m 0755 "$BUILT_GUI" "$BIN_DIR/$APP_ID"

# Mode 0755 root:root is mandatory: pkexec refuses to execute a program
# that is group- or world-writable, or not owned by root.
info "Installing root helper: $HELPER_RUNTIME_PATH"
install -d "${own[@]}" -m 0755 "$LIB_DIR"
install "${own[@]}" -m 0755 "$BUILT_HELPER" "$LIB_DIR/$HELPER_NAME"

info "Installing icons and desktop entry"
install -d "${own[@]}" -m 0755 "$SHARE_DIR" "$APPS_DIR" "$ICON_DIR/128x128/apps" "$ICON_DIR/scalable/apps"
install "${own[@]}" -m 0644 icon.png "$SHARE_DIR/icon.png"
install "${own[@]}" -m 0644 icon.png "$ICON_DIR/128x128/apps/$APP_ID.png"
install "${own[@]}" -m 0644 icon.svg "$ICON_DIR/scalable/apps/$APP_ID.svg"
install "${own[@]}" -m 0644 "$APP_ID.desktop" "$APPS_DIR/$APP_ID.desktop"

# Every action id in the policy must be known to the rules file (an allowlist, not a namespace prefix).
for id in $(grep -o 'action id="com\.ryzencurveoptimizer\.[^"]*"' "$POLICY_NAME" | cut -d'"' -f2); do
    grep -qF "\"$id\"" <(grep -v '^ *\*' "$RULES_NAME") \
        || die "polkit action $id is in $POLICY_NAME but not in the rules allowlist ($RULES_NAME)"
done

info "Installing polkit policy: $POLKIT_ACTION_DIR/$POLICY_NAME"
install -d "${own[@]}" -m 0755 "$POLICY_DIR" "$RULES_DIR"
install "${own[@]}" -m 0644 "$POLICY_NAME" "$POLICY_DIR/$POLICY_NAME"
# The annotation ships with the /usr default; rewrite it to where the helper actually landed.
# If this path and the pkexec argv ever disagree, the action silently stops being enforced.
sed -i \
    "s#<annotate key=\"org.freedesktop.policykit.exec.path\">[^<]*</annotate>#<annotate key=\"org.freedesktop.policykit.exec.path\">$HELPER_RUNTIME_PATH</annotate>#" \
    "$POLICY_DIR/$POLICY_NAME"
grep -qF ">$HELPER_RUNTIME_PATH<" "$POLICY_DIR/$POLICY_NAME" || die "failed to set exec.path in the installed policy file"

info "Installing polkit rules (security level $SECLEVEL): $POLKIT_RULESDIR/$RULES_NAME"
sed -e "s|@HELPER@|$HELPER_RUNTIME_PATH|g" -e "s|@SECLEVEL@|$SECLEVEL|g" "$RULES_NAME" > "$RULES_DIR/$RULES_NAME"
chmod 0644 "$RULES_DIR/$RULES_NAME"
[ -n "$DESTDIR" ] || chown root:root "$RULES_DIR/$RULES_NAME"

# ── Migration from the Python/ryzenadj version ───────────────────────
# Runs only after the new files are installed. Everything it removes is checked to
# be the OLD thing first (a python script, a .py helper); a new ELF install is never touched.
migrate_legacy() {
    [ "${#LEGACY_FILES[@]}" -gt 0 ] || return 0
    echo
    info "Migrating the earlier (Python/ryzenadj) install"

    # 1. Prove the new install works before removing anything old.
    [ -x "$PREFIX/lib/$APP_ID/$HELPER_NAME" ] && [ "$(stat -c '%u %a' "$PREFIX/lib/$APP_ID/$HELPER_NAME")" = "0 755" ] \
        || die "new helper is not root:root 0755; leaving the old install in place"
    [ "$(head -c4 "$PREFIX/bin/$APP_ID" | tail -c3)" = "ELF" ] \
        || die "$PREFIX/bin/$APP_ID is not the new binary; leaving the old install in place"
    # Ask the running polkit what it pinned (it re-reads the actions directory on its own, so give it a moment).
    # No answer at all (no daemon, e.g. in a chroot) is not a failure; a wrong answer is.
    if command -v pkaction >/dev/null 2>&1; then
        local out="" i
        for i in 1 2 3 4 5 6; do
            out="$(pkaction --action-id "$ACTION_ID" --verbose 2>/dev/null || true)"
            [ -z "$out" ] || grep -qF "$HELPER_RUNTIME_PATH" <<<"$out" && break
            sleep 0.5
        done
        [ -z "$out" ] || grep -qF "$HELPER_RUNTIME_PATH" <<<"$out" \
            || die "polkit does not pin $ACTION_ID to $HELPER_RUNTIME_PATH; leaving the old install in place"
    fi

    # 2. Old programs. Only the python pieces; the polkit policy was already replaced above.
    local f b d
    # Re-checked at removal time: $PREFIX/bin/$APP_ID was a python script when detected and is the new
    # binary by now, so only what is still a python script / a .py file goes.
    for f in "${LEGACY_FILES[@]}"; do
        case "$f" in
            "$POLKIT_ACTION_DIR/$POLICY_NAME") echo "   replaced $f (same action id, new helper path)" ;;
            */bin/$APP_ID)
                if is_python_script "$f"; then rm -f "$f" && echo "   removed $f"
                else echo "   replaced $f (python script -> new binary)"; fi ;;
            *.py) rm -f "$f" && echo "   removed $f" ;;
        esac
    done
    for b in "${LEGACY_BASES[@]}"; do
        rm -rf "$b/lib/$APP_ID/__pycache__"
        # The old helper dir goes only when nothing else lives in it; at the new PREFIX it holds the new helper.
        rmdir "$b/lib/$APP_ID" 2>/dev/null && echo "   removed $b/lib/$APP_ID" || true
        if [ "$b" != "$PREFIX" ]; then
            # Older layout under /usr/local only: icon copy, menu entry, icons.
            for d in "$b/share/$APP_ID"; do [ -d "$d" ] && rm -rf "$d" && echo "   removed $d"; done
            for f in "$b/share/applications/$APP_ID.desktop" "$b/share/icons/hicolor/128x128/apps/$APP_ID.png" \
                     "$b/share/icons/hicolor/scalable/apps/$APP_ID.svg"; do
                [ -f "$f" ] && rm -f "$f" && echo "   removed $f"
            done
        fi
    done

    # 3. Saved profiles: same directory, same JSON, nothing to move. Report, never touch.
    local home n
    for home in /root /home/*; do
        [ -d "$home" ] && [ ! -L "$home" ] || continue
        n="$( { find "$home/.config/$APP_ID/profiles" -maxdepth 1 -name '*.json' -type f 2>/dev/null || true; } | wc -l)"
        [ "$n" -gt 0 ] && echo "   kept $n saved profile(s) in $home/.config/$APP_ID/profiles (same format)"
    done

    echo "   The old tool needed ryzenadj and PySide6; the new one needs neither (they can be uninstalled)."
    echo "   The new one talks to the SMU natively: it needs the ryzen_smu module (or writable PCI config space)."
    pgrep -f "ryzen_curve_optimizer.py" >/dev/null 2>&1 \
        && echo "   !! an old Python window is still running: close it and start the new one."
    echo "   Polkit security level $SECLEVEL applies (the old tool asked for the password once per cache window = level 2)."
    return 0
}
[ -n "$DESTDIR" ] || migrate_legacy

# ── Refresh desktop/icon caches ───────────────────────────────────────
if [ -z "$DESTDIR" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
fi

# ── Runtime dependency report ─────────────────────────────────────────
echo
info "Checking runtime dependencies"
if [ -z "$DESTDIR" ]; then
    dep() { printf '   [%s] %-34s %s\n' "$( ( eval "$2" ) >/dev/null 2>&1 && echo ok || echo -- )" "$1" "$3"; }
    # grep -c reads all of ldconfig's output: grep -q exits on the first match, ldconfig then dies of
    # SIGPIPE and pipefail reports a library that is present as missing.
    lib()  { ldconfig -p | grep -c "$1" >/dev/null; }
    kmod() { [ -d "/sys/module/$1" ] || modinfo "$1" >/dev/null 2>&1; }
    dep "polkit (pkexec)"              "command -v pkexec"                         "required — runs the root helper"
    dep "Qt6 Widgets/Svg"              "lib libQt6Widgets.so.6 && lib libQt6Svg.so.6" "required — GUI"
    dep "ryzen_smu >= 0.1.7"           "kmod ryzen_smu"                            "needed under Secure Boot / kernel lockdown"
    dep "or writable PCI config space" "[ -w /sys/bus/pci/devices/0000:00:00.0/config ]" "works only without lockdown"
    dep "msr module"                   "kmod msr"                                  "optional — 'VID + power' live monitor"
    probe="$(echo '{"op":"probe"}' | "$LIB_DIR/$HELPER_NAME" 2>/dev/null || true)"
    echo "   helper probe: ${probe:-no answer}"
else
    echo "   (staged install: not checked)"
fi

echo
echo "Installation finished (polkit security level $SECLEVEL)."
echo "Run it from a terminal with:  $APP_ID"
echo "...or launch 'Ryzen Curve Optimizer' from your application menu."
echo "It runs unprivileged; pkexec prompts only when you apply a change."

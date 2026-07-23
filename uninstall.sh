#!/bin/bash
# uninstall.sh — Ryzen Curve Optimizer uninstaller
# Usage: sudo ./uninstall.sh            (keeps saved profiles)
#        sudo ./uninstall.sh --purge    (also deletes saved profiles)

set -e

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root: sudo ./uninstall.sh"
    exit 1
fi

PURGE=0
if [ "${1:-}" = "--purge" ]; then
    PURGE=1
fi

HELPER_DIR="/usr/local/lib/ryzen-curve-optimizer"
SHARE_DIR="/usr/local/share/ryzen-curve-optimizer"
POLICY_FILE="/usr/share/polkit-1/actions/com.ryzencurveoptimizer.policy"

echo "-> Removing root helper: $HELPER_DIR"
rm -rf "$HELPER_DIR"

echo "-> Removing icon: $SHARE_DIR"
rm -rf "$SHARE_DIR"

echo "-> Removing polkit policy: $POLICY_FILE"
rm -f "$POLICY_FILE"

if [ "$PURGE" -eq 1 ]; then
    # Saved profiles live under each user's own $XDG_CONFIG_HOME (or
    # ~/.config as a fallback), so there's no single system-wide path to
    # remove them from — walk every real user's home directory instead
    # (root included, in case the applet was ever run as root).
    echo "-> Purging saved profiles for all users..."
    while IFS=: read -r _ _ uid _ _ home _; do
        if { [ "$uid" -ge 1000 ] || [ "$uid" -eq 0 ]; } && [ -d "$home" ]; then
            profile_dir="$home/.config/ryzen-curve-optimizer"
            if [ -d "$profile_dir" ]; then
                echo "   removing $profile_dir"
                rm -rf "$profile_dir"
            fi
        fi
    done < /etc/passwd
else
    echo "-> Saved profiles were left in place (~/.config/ryzen-curve-optimizer/)."
    echo "   Re-run with --purge to remove them too."
fi

echo "-> Done."

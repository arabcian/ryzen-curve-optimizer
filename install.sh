#!/bin/bash
# install.sh — Ryzen Curve Optimizer installer
# Usage: sudo ./install.sh

set -e

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root: sudo ./install.sh"
    exit 1
fi

HELPER_DIR="/usr/local/lib/ryzen-curve-optimizer"
SHARE_DIR="/usr/local/share/ryzen-curve-optimizer"
POLICY_DIR="/usr/share/polkit-1/actions"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "-> Installing root helper: $HELPER_DIR"
mkdir -p "$HELPER_DIR"
install -o root -g root -m 0755 "$SCRIPT_DIR/ryzen_curve_optimizer_helper.py" "$HELPER_DIR/ryzen_curve_optimizer_helper.py"

echo "-> Installing icon: $SHARE_DIR"
mkdir -p "$SHARE_DIR"
install -o root -g root -m 0644 "$SCRIPT_DIR/icon.png" "$SHARE_DIR/icon.png"

echo "-> Installing polkit policy: $POLICY_DIR"
install -o root -g root -m 0644 "$SCRIPT_DIR/com.ryzencurveoptimizer.policy" "$POLICY_DIR/com.ryzencurveoptimizer.policy"

echo "-> Done."
echo ""
echo "To run the application (no root needed — it will prompt via pkexec only when applying changes):"
echo "  python3 $SCRIPT_DIR/ryzen_curve_optimizer.py"
echo ""
echo "Requirements: PySide6 (pip install --user PySide6, or your distro's package), ryzenadj must be on PATH."

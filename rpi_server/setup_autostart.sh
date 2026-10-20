#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  FarmCam - Automatic Startup Setup for RPi 5
#  Configures both:
#    1. main.py (Detection Server) via farmcam.service
#    2. display_server.py + HDMI Kiosk via farmcam-display.service
#
#  Usage:
#     cd rpi_server
#     chmod +x setup_autostart.sh
#     sudo ./setup_autostart.sh
# ═══════════════════════════════════════════════════════════════

set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run with sudo: sudo ./setup_autostart.sh"
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# Determine the desktop user (cannot be root)
DESKTOP_USER="${SUDO_USER:-$1}"
if [ -z "$DESKTOP_USER" ] || [ "$DESKTOP_USER" = "root" ]; then
    # Try finding the primary logged-in or uid 1000 user
    DESKTOP_USER=$(id -un 1000 2>/dev/null || echo "")
fi

if [ -z "$DESKTOP_USER" ] || [ "$DESKTOP_USER" = "root" ]; then
    echo "⚠️  Could not automatically detect your non-root desktop user."
    read -p "Enter the Raspberry Pi desktop username (e.g. pi or farmer): " DESKTOP_USER
fi

echo "=========================================================="
echo "🌿 FarmCam Auto-Start Setup (Detection + HDMI Display)"
echo "   Desktop User: $DESKTOP_USER"
echo "   Server Path : $SCRIPT_DIR"
echo "=========================================================="
echo ""

# 1. Install prerequisites
echo "📦 Step 1: Checking system packages (chromium, venv)..."
apt update
apt install -y chromium python3-venv

# 2. Check or set up farmcam.service (main.py)
echo ""
echo "⚙️  Step 2: Checking detection service (farmcam.service)..."
if [ ! -f /etc/systemd/system/farmcam.service ]; then
    echo "   Creating /etc/systemd/system/farmcam.service..."
    if [ -f "$SCRIPT_DIR/farmcam_env/bin/python" ]; then
        PYTHON_EXEC="$SCRIPT_DIR/farmcam_env/bin/python"
    elif [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
        PYTHON_EXEC="$SCRIPT_DIR/venv/bin/python"
    else
        PYTHON_EXEC=$(which python3)
    fi
    echo "   Using Python: $PYTHON_EXEC"
    cat > /etc/systemd/system/farmcam.service << EOF
[Unit]
Description=FarmCam Lettuce Pest Detection Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart="$PYTHON_EXEC" "$SCRIPT_DIR/main.py"
Restart=always
RestartSec=5
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
fi

# Enable farmcam service
systemctl enable farmcam.service
echo "   ✅ farmcam.service is enabled."

# 3. Run the display installer
echo ""
echo "🖥️  Step 3: Installing HDMI Display Kiosk service and autostart..."
python3 install_display.py --user "$DESKTOP_USER"

# 4. Reload and restart services
echo ""
echo "🔄 Step 4: Starting services..."
systemctl daemon-reload
systemctl restart farmcam-display
systemctl start farmcam || true

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✅ All auto-start services configured successfully!"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  Status:"
systemctl status farmcam --no-pager -n 3 || true
echo "  ---"
systemctl status farmcam-display --no-pager -n 3 || true
echo ""
echo "  📌 Next steps:"
echo "  1. Ensure your micro-HDMI cable is plugged into HDMI 0"
echo "     (the micro-HDMI port closest to the USB-C power port)."
echo "  2. Ensure desktop auto-login is enabled in raspi-config."
echo "  3. Reboot your Pi to test full auto-start on HDMI:"
echo "       sudo reboot"
echo ""

#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  FarmCam - RPi5 One-Time Setup Script
#  Run this ONCE on your Raspberry Pi 5:
#
#     chmod +x setup.sh
#     sudo ./setup.sh
# ═══════════════════════════════════════════════════════════════

echo "🌿 FarmCam RPi5 Setup"
echo "====================="

# 1. Update system
echo ""
echo "📦 Step 1: Updating system..."
sudo apt update && sudo apt upgrade -y

# 2. Install system dependencies
echo ""
echo "📦 Step 2: Installing dependencies..."
sudo apt install -y python3-pip python3-venv python3-opencv libcap-dev

# 3. Create virtual environment
echo ""
echo "📦 Step 3: Creating Python environment..."
cd "$(dirname "$0")"
python3 -m venv --system-site-packages farmcam_env
source farmcam_env/bin/activate

# 4. Install Python packages
echo ""
echo "📦 Step 4: Installing Python packages (LOW STORAGE MODE)..."
pip cache purge
pip install --no-cache-dir fastapi uvicorn opencv-python-headless numpy ultralytics

# 5. Create systemd service (auto-start on boot)
echo ""
echo "⚙️  Step 5: Setting up auto-start..."
SCRIPT_DIR=$(pwd)

cat > /etc/systemd/system/farmcam.service << EOF
[Unit]
Description=FarmCam Lettuce Pest Detection Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart="$SCRIPT_DIR/farmcam_env/bin/python" "$SCRIPT_DIR/main.py"
Restart=always
RestartSec=5
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable farmcam.service

echo ""
echo "═══════════════════════════════════════════════"
echo "  ✅ FarmCam Setup Complete!"
echo "═══════════════════════════════════════════════"
echo ""
echo "  To START now:    sudo systemctl start farmcam"
echo "  To STOP:         sudo systemctl stop farmcam"
echo "  To check LOGS:   sudo journalctl -u farmcam -f"
echo "  Before a manual run: sudo systemctl stop farmcam"
echo "  To RUN manually: ./farmcam_env/bin/python main.py"
echo ""
echo "  What happens next:"
echo "  1. RPi5 will create 'FarmCam-Setup' WiFi"
echo "  2. Connect your phone to it"
echo "  3. Open FarmCam app → enter your hotspot info"
echo "  4. RPi5 auto-connects to your hotspot forever!"
echo ""

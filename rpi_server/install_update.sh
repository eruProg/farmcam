#!/usr/bin/env bash
# FarmCam update: retrained pest + fungus models (HEF) and the matching server code.
#
#   cd ~/pi_update && sudo bash install_update.sh          # install
#   cd ~/pi_update && sudo bash install_update.sh --rollback   # back to how it was
#
# The server folder is found from farmcam.service (WorkingDirectory). data/
# (the detection database), captures and last_pump_time.txt are never touched.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="$(systemctl show -p WorkingDirectory --value farmcam 2>/dev/null || true)"
if [ -z "$TARGET" ] || [ ! -f "$TARGET/main.py" ]; then
  echo "Could not find the FarmCam server folder from farmcam.service."; exit 1
fi
DROPIN=/etc/systemd/system/farmcam.service.d
CONF="$DROPIN/farmcam-models.conf"
BACKUP="$TARGET/backup_before_update"
POWER_SUDOERS=/etc/sudoers.d/farmcam-power

show_log() {
  echo "Waiting for the server to start..."
  sleep 30
  journalctl -u farmcam -n 120 --no-pager \
    | grep -E "DET loaded|SEG loaded|failed|Build:|pest model|Whitefly classifier|Inference error|Traceback" || true
}

if [ "${1:-}" = "--rollback" ]; then
  [ -d "$BACKUP" ] || { echo "No backup found at $BACKUP"; exit 1; }
  systemctl stop farmcam
  for hef in best_detect.hef best_seg.hef whitefly_cls.onnx; do
    if [ -f "$BACKUP/models/$hef.absent" ]; then rm -f "$TARGET/models/$hef"; fi
  done
  # Backups made before the classifier existed have no marker for it.
  [ -f "$BACKUP/files/models/whitefly_cls.onnx" ] || rm -f "$TARGET/models/whitefly_cls.onnx"
  cp -a "$BACKUP/files/." "$TARGET/"
  rm -f "$CONF" "$POWER_SUDOERS"
  systemctl daemon-reload
  systemctl start farmcam
  systemctl restart farmcam-display 2>/dev/null || true
  echo "Rolled back to the previous models and code."
  show_log
  exit 0
fi

echo "Installing into $TARGET"
systemctl stop farmcam
if [ ! -d "$BACKUP" ]; then            # keep the FIRST backup: the original working state
  mkdir -p "$BACKUP/files/models" "$BACKUP/models"
  cp -a "$TARGET"/*.py "$BACKUP/files/" 2>/dev/null || true
  [ -d "$TARGET/display" ] && cp -a "$TARGET/display" "$BACKUP/files/"
  for hef in best_detect.hef best_seg.hef whitefly_cls.onnx; do
    if [ -f "$TARGET/models/$hef" ]; then cp -a "$TARGET/models/$hef" "$BACKUP/files/models/"
    else touch "$BACKUP/models/$hef.absent"; fi
  done
  echo "Backup saved in $BACKUP"
fi
cp -a "$HERE/server/." "$TARGET/"
mkdir -p "$TARGET/models"
cp "$HERE/models/best_detect.hef" "$HERE/models/best_seg.hef" "$TARGET/models/"
# Second deep-learning stage for whiteflies (only in updates that ship it).
if [ -f "$HERE/models/whitefly_cls.onnx" ]; then
  cp "$HERE/models/whitefly_cls.onnx" "$TARGET/models/"
  echo "Whitefly classifier installed (models/whitefly_cls.onnx)."
fi
mkdir -p "$DROPIN"
cat > "$CONF" <<EOF
[Service]
Environment=FARMCAM_PEST_MODEL=natural
Environment=FARMCAM_FUNGUS_MODEL=natural2
EOF
# Shut down / Restart from the app. A root service runs systemctl itself; any
# other service user may run exactly these commands through sudo, no password.
SVC_USER="$(systemctl show -p User --value farmcam 2>/dev/null || true)"
if [ -n "$SVC_USER" ] && [ "$SVC_USER" != "root" ]; then
  TMP_SUDOERS="$(mktemp)"
  cat > "$TMP_SUDOERS" <<EOF
# Managed by FarmCam install_update.sh: Shut down / Restart from the phone app.
$SVC_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/sbin/shutdown -h now, /usr/sbin/shutdown -r now
EOF
  if visudo -cf "$TMP_SUDOERS" >/dev/null; then
    install -m 0440 -o root -g root "$TMP_SUDOERS" "$POWER_SUDOERS"
    echo "Power: '$SVC_USER' may shut down / restart the Pi from the app."
  else
    echo "Power: could not set up shut down / restart for '$SVC_USER'."
  fi
  rm -f "$TMP_SUDOERS"
  if [ "$(systemctl show -p NoNewPrivileges --value farmcam 2>/dev/null)" = "yes" ]; then
    echo "Power: farmcam.service has NoNewPrivileges=yes, which blocks shut down / restart from the app."
  fi
else
  echo "Power: FarmCam runs as root and can shut down / restart the Pi from the app."
fi
# HDMI display without a password: boot straight to the desktop, logged in
# (the kiosk starts from the desktop autostart). Undo: sudo raspi-config >
# System Options > Boot / Auto Login.
if command -v raspi-config >/dev/null 2>&1; then
  if raspi-config nonint do_boot_behaviour B4; then
    echo "Display: desktop auto-login on (no password at start-up)."
  else
    echo "Display: could not turn on auto-login; set it in sudo raspi-config."
  fi
fi
systemctl daemon-reload
systemctl start farmcam
systemctl restart farmcam-display 2>/dev/null || true
show_log
echo
echo "Check above: 'DET loaded', 'SEG loaded', 'pest model: natural; fungus model: natural2'"
echo "and 'Whitefly classifier: whitefly_cls.onnx'."
echo "If anything is wrong: sudo bash install_update.sh --rollback"

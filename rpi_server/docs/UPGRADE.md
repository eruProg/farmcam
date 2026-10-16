# Install the October 16 FarmCam update

Update the Raspberry Pi server, ESP32 firmware, and Android app together. The nominal scan is 333 seconds; the buzzer is on ESP32 GPIO 26. Software tests pass, but camera/model accuracy and physical actuation still need acceptance on your rig.

## Raspberry Pi 5

Keep the existing Hailo runtime, camera configuration, HEF files, databases, and service settings. An OS reflash is unnecessary. Do not rerun the broad setup.sh script just for this update.

Find the current service directory:

```sh
systemctl show farmcam -p WorkingDirectory -p ExecStart
```

In the existing repository root (the parent of rpi_server), stop the service and back up the server folder before changing files:

```sh
sudo systemctl stop farmcam
sudo tar -czf "$HOME/farmcam-server-backup-$(date +%Y%m%d-%H%M%S).tar.gz" rpi_server
git status --short
git pull --ff-only origin master
```

If Git reports local changes or a divergent branch, stop and preserve those changes; do not reset or force-pull. If your Pi only has a copied server folder, download the updated repository and copy the updated rpi_server source into the existing service folder while keeping its models/, data/, detection_logs.db, farmcam_env/, and private environment settings. The repository is public; no GitHub sign-in is required to download it.

Use the service's existing virtual environment. The commands below assume the supplied farmcam_env layout:

```sh
cd rpi_server
./farmcam_env/bin/python -m pip install -r requirements.txt
sudo systemctl edit farmcam
```

Add these settings to the systemd override, replacing the example values with the ESP32 address shown in its Serial Monitor and your actual recipient:

```ini
[Service]
Environment="FARMCAM_ESP32_IP=192.168.1.50"
Environment="FARMCAM_FARMER_PHONE=+639123456789"
```

Save, then start the service after the updated ESP32 has joined the same network:

```sh
sudo systemctl daemon-reload
sudo systemctl restart farmcam
sudo systemctl status farmcam --no-pager
journalctl -u farmcam -n 80 --no-pager
curl -f http://127.0.0.1:8000/api/scan/state
curl -f http://127.0.0.1:8000/api/config
curl -f http://127.0.0.1:8000/api/detection/debug
```

The debug endpoint should report build `2026-10-16-farmcam-complete-1`. Existing tracks/history are retained; old summary-only history is not converted into invented track geometry. If the service uses a different Python environment, use the interpreter shown in ExecStart. Do not start a second main.py while the service owns the camera/Hailo device.

## ESP32

Yes, upload the updated sketch once. The old firmware cannot provide the new correlated HTTP SMS acknowledgement protocol.

Open `rpi_server/farmcam_esp32/farmcam_esp32.ino` in Arduino IDE. Use ESP32 Dev Module (or the exact board selection for your device) with Espressif ESP32 core 3.3.11, which was used for the compile check. Select the USB port belonging to your ESP32.

Copy `device_config.example.h` to `device_config.h` in the same sketch folder and enter the hotspot SSID/password. The local workstation already has a private device_config.h preserving your earlier settings. Git ignores this file. FARMER_PHONE is used only by legacy SMS commands; the updated Pi supplies its recipient using FARMCAM_FARMER_PHONE.

Upload the sketch, then open Serial Monitor at 115200 baud and record its Wi-Fi IP. Verify from the Pi:

```sh
curl -f http://ESP32_IP/status
curl -f 'http://ESP32_IP/sms/status?id=probe'
```

Replace ESP32_IP with the real address. The second response should contain the requested ID and a boot_id; an unknown status for the probe is expected. Wiring remains relay GPIO 27, buzzer GPIO 26, UART RX GPIO 16, and UART TX GPIO 17. Use the established powered hardware test setup when validating pump behavior.

## Android phone

Download FarmCam-1.0.0-build2.apk from the repository's prerelease, or copy `build/app/outputs/flutter-apk/app-release.apk` from this workstation. Transfer it by USB or your usual file-transfer method, open it on the phone, and allow installation from that source when Android asks. Join the same network as the Pi/ESP32, open FarmCam, and enter the Pi IP with port 8000.

This APK is release-mode and signed with a dedicated local FarmCam key. If Android rejects installation because a previous debug build has a different signature, uninstall that old app first; local app settings will be reset. Pi databases are unaffected. Back up `android/farmcam-release.jks` and `android/key.properties` privately so future updates can retain the same signature. Neither file is committed to GitHub. Other developers must supply their own signing configuration to build a signed release.

## Accuracy and acceptance

The UI implementation is complete and has been checked on phone/tablet layouts, enlarged text, disconnected states, and failed requests. It still needs a real-phone check against the live Pi stream.

This update improves frame attribution, filtering, persistence, and actuation behavior. It does not retrain or change the frozen inference functions. No new model accuracy percentage is claimed. Verify camera distance/focus, station ROIs, lighting, class labels, and known healthy/pest/fungus examples. Review detections in the app; its precision display uses user-reviewed tracks and does not measure missed detections or full model accuracy. Follow HARDWARE_ACCEPTANCE.md and record results before relying on automatic operation.


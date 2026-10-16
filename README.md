# FarmCam

FarmCam monitors four lettuce stations using Raspberry Pi 5, Hailo inference, an ESP32 hardware bridge, and a Flutter client. It retains confirmed detections across restarts, records completed upper/lower scans, and controls a timed pesticide pump, buzzer, and SIM800L notifications.

## Run the client

Install Flutter, then run:

```sh
flutter pub get
flutter run
```

Use the Raspberry Pi address and port `8000` on the connection screen. The optional viewer runs on port `8080`. The browser client can be built with `flutter build web`; live MJPEG and device networking should be validated in the intended browser. Android is the primary hardware client.

```sh
flutter analyze
flutter test
flutter build apk --debug
```

The Android project uses AGP 8.11.1, Kotlin 2.2.20, and Gradle 8.14. Debug APKs are suitable for testing. Release builds use the ignored android/key.properties and a private keystore; see the upgrade guide for installation and signing notes. The iOS scaffold and launcher assets are included; run Flutter package restoration and build/sign with macOS/Xcode for iOS (not verified on this Windows workstation).

## Run the Raspberry Pi service

Use Raspberry Pi OS with Picamera2 and the installed Hailo runtime. Put your existing HEF model files under `rpi_server/models/`; this repository does not include model binaries.

```sh
cd rpi_server
python3 -m venv --system-site-packages farmcam_env
. farmcam_env/bin/activate
pip install -r requirements.txt
export FARMCAM_ESP32_IP=192.168.1.50
export FARMCAM_FARMER_PHONE=+639123456789
python main.py
```

Set the phone number to the actual recipient. The example address and number are illustrative. With systemd, put the same values in the service environment. Keep the control API on a trusted local network: it has no authentication and must not be exposed to the public internet.

Flash `rpi_server/farmcam_esp32/farmcam_esp32.ino` after configuring its existing Wi-Fi settings. This is the canonical firmware; older standalone sketches are retained for reference. The updated server requires its `/sms/status?id=...` protocol for automatic SMS. No firmware is flashed by the software tests.

## Verification

Python tests use temporary databases, synthetic images, simulated time, and mocked actuators:

```sh
python -m pip install -r rpi_server/requirements-dev.txt
python -m pytest rpi_server/tests rpi_server/test_pump_control.py rpi_server/test_detection.py rpi_server/test_repair_service.py rpi_server/test_check_fungus.py
```

Flutter tests include pump release during a pending request, API error handling, monotonic countdowns, and layouts at 360 and 768 logical pixels with normal and enlarged text. Layout previews are written to `.verification/ui/`.

For the coordinated Pi, ESP32, and Android update, follow [Upgrade instructions](rpi_server/docs/UPGRADE.md).

For the non-touch HDMI monitor, follow [Automatic display setup](rpi_server/docs/HDMI_DISPLAY.md). It opens a read-only overview at boot and keeps a waiting/reconnection screen available independently of detection.

See [System overview](rpi_server/docs/SYSTEM_OVERVIEW.md), [Hardware acceptance](rpi_server/docs/HARDWARE_ACCEPTANCE.md), and [Completion status](missing.md).

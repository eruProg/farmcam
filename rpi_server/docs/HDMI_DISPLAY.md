# Automatic HDMI monitoring display

FarmCam can open a read-only monitoring screen automatically on the Pi desktop. The display starts independently of the detection server and shows **Waiting for the system to start…** until the backend responds. It keeps the live camera, four station summaries, scan/countdown, recent confirmed detections, and equipment status on one screen. No mouse, keyboard, or touch input is needed during normal operation.

## Connect and install

Use a micro-HDMI cable/adapter at the Raspberry Pi 5 end and the connector required by your monitor at the other end. Supply monitor power according to the monitor's instructions. Keep the existing Pi OS desktop, camera/Hailo installation, models, databases, and desktop auto-login. No OS reflash is required.

Update your existing checkout following UPGRADE.md. From the repository root, install only these desktop packages if they are missing:

```sh
sudo apt install chromium python3-venv
cd rpi_server
sudo python3 install_display.py --user "$USER"
sudo systemctl restart farmcam
sudo reboot
```

Run the installer as the user who logs into the graphical desktop. If using SSH under a different account, replace `"$USER"` with the desktop username. The installer detects a running labwc, Wayfire, or X11 session. If no session is running, specify the known session explicitly, for example:

```sh
sudo python3 install_display.py --user farmer --session labwc
```

Do not guess the session or use `root` as the desktop user. The installer requires an existing `farmcam.service` and preserves its interpreter, environment, and hardware settings. It enables that service at boot and adds failure restart behavior only if missing. It does not launch a second `main.py`.

The installer creates a separate `.display_env` using requirements-display.txt, installs `farmcam-display.service`, and adds one managed desktop launcher. First installation needs package access; normal monitoring requires neither internet nor Wi-Fi. The browser and its supervisor run as the desktop user. Service and startup originals are backed up in `/var/lib/farmcam-display/install.json`. Running the installer again does not add duplicate startup entries.

## What appears after power-on

- Before the backend is reachable: **Waiting for the system to start…**
- Backend reachable but not ready: **Starting monitoring…**
- Ready: **Monitoring**, the current station/phase, and a local countdown.
- Camera older than three seconds: **Camera unavailable**, covering the old image.
- Detection-loop heartbeat older than ten seconds or coordinator fault: **Detection interrupted**.
- Backend lost after connecting: **Connection lost — reconnecting…**, with last known data clearly marked.
- History unavailable: an explicit history error; a successful empty result says **No detections recorded**.

Unscanned stations say **Not scanned yet**, even if the older station API supplies initial zero counts. Station results represent completed scans, not a guarantee that no new threat exists between visits. Recent logs exclude records marked false positive and show three rows at 1024×600, up to five on taller screens. Full history/review and manual actions remain in the phone app.

## Operation and recovery

The display uses `http://127.0.0.1:8090/` locally. It serves bundled assets and reads only the fixed backend status, health, SMS status, log, and display-stream endpoints on port 8000. It does not import inference code, own the camera, open the database, or issue actuator commands. Port 8080 retains the existing diagnostic viewer.

The added backend endpoints are GET `/api/display/health` and GET `/api/display/stream`. The latter encodes existing verified-overlay frames at at most eight frames per second, capped at 960 pixels wide. The mobile stream's defaults remain unchanged. The display service exposes only GET `/`, `/assets/{allowed-name}`, `/api/state`, and `/stream`; it is not a general proxy and binds only to loopback.

Status polling runs every two seconds after the previous request completes; history every five. Requests have bounded timeouts. A process-instance ID resets the browser's monotonic countdown synchronization after a server restart. Failed requests do not create concurrent polling or stream loops. One display-stream connection is allowed at a time; old connections are closed before reconnecting.

The kiosk uses its own Chromium profile, an exclusive process lock, and a restart supervisor. Browser crashes are retried after five seconds. A browser screen wake lock prevents compositor blanking; the supervisor additionally inhibits logind idle/suspend and disables X11 blanking for its session. Verify wake-lock behavior on the actual Pi compositor during acceptance; do not assume a Windows browser test establishes this.

Check the services over SSH or a maintenance terminal:

```sh
systemctl status farmcam farmcam-display --no-pager
journalctl -u farmcam-display -n 60 --no-pager
journalctl -u farmcam -n 60 --no-pager
curl -f http://127.0.0.1:8090/api/state
curl -f http://127.0.0.1:8000/api/display/health
```

To restart the display service:

```sh
sudo systemctl restart farmcam-display
```

To close the full-screen window for maintenance, run as the desktop user:

```sh
python3 kiosk_runner.py --stop
```

Reopen it from a terminal in the desktop session with `python3 kiosk_runner.py`, or log out/in. Do not launch it with sudo. The browser launcher waits for the local display service, not for detection or an internet connection.

## Uninstall

```sh
sudo python3 install_display.py --user "$USER" --uninstall
```

This stops the kiosk/display service, removes the managed launcher, and restores saved service/startup settings. Later edits outside the managed block are retained. If a generated service file was edited independently, uninstall stops and reports its backup instead of overwriting the edits. Detection is not stopped; if it was originally disabled at boot, that setting is restored. Log out/in to restore session-only X11 blanking defaults. The display virtual environment and browser profile are retained for reinstall; models/databases are untouched.

## Verification

Local automated checks cover the independent waiting page, readiness, backend loss/recovery/restart, stale camera, detection stall, empty/unavailable logs, polling overlap, fixed upstream routes, stream cleanup, installer backup/rerun/restoration, and frozen inference compatibility. Browser checks use synthetic camera and detection fixtures, not real accuracy measurements.

```sh
python -m pytest rpi_server/tests rpi_server/test_pump_control.py rpi_server/test_detection.py rpi_server/test_repair_service.py rpi_server/test_check_fungus.py
# With Playwright installed and display_server.py running locally:
node rpi_server/tests/display_browser.cjs
```

Browser verification defaults to Edge on this development workstation. Set `BROWSER_CHANNEL=chromium` for an installed Playwright Chromium. Screenshots go to `.verification/display/`. Layout checks cover 1024×600, 1280×800, and 1920×1080 with normal and 125% typography.

Connected-hardware acceptance remains pending:

- Boot with detection stopped/disabled; confirm the waiting screen appears without keyboard input. Start detection and confirm automatic transition to monitoring.
- Reboot normally and verify exactly one detection process, one display service, and one browser supervisor. Rerun the installer and repeat.
- Stop/restart detection, disconnect/reconnect the camera, and restart the browser/display service. Confirm frozen frames are covered and recovery requires no clicks.
- Run without Wi-Fi/internet; local camera, station results, and history must continue. Equipment/SMS connectivity depends on the actual ESP32 network.
- Leave the monitor on beyond its configured idle timeout and confirm it does not blank. Check cold boot, monitor power cycling, native resolution, readable text, and no scrolling.
- Measure inference-frame throughput and CPU/memory over a full 333-second cycle without the kiosk, then repeat with it. Record both values and investigate meaningful regression before deployment.
- Confirm the phone app remains usable simultaneously and run the existing pump-release/heartbeat and buzzer acceptance checks.

No Android APK or ESP32 firmware update is needed for this display feature. Deploy the updated Pi source and run the installer on the actual Pi; local development does not install services on your hardware.

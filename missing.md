# FarmCam completion status

Verified on October 15, 2026. Software implementation and local checks are complete; connected-hardware acceptance remains pending.

October 16 release preparation: dedicated-key signed Android release APK 1.0.0 (build 2) built and signature-verified. Upgrade instructions are in rpi_server/docs/UPGRADE.md. Private device settings and signing keys are excluded from Git.

## Backend and hardware integration

- [x] Seven-phase state machine is the sole scan controller, advances without camera frames, and applies the longer return after station 4. Nominal cycle: **333 seconds**.
- [x] Immutable capture metadata, stale-frame rejection, phase-boundary result rejection, and inference suppression during movement/settling.
- [x] Explicit runtime dependency injection, partial-startup cleanup, repeatable shutdown, and restart recovery.
- [x] Accumulate complete detection windows, commit once, resolve valid empty rescans, and abort incomplete windows without clearing previous tracks.
- [x] Correct persistence contracts, one-to-one track matching, real track-ID snapshots, and restored station summaries.
- [x] Atomic persisted pump/SMS cooldowns with 300-second defaults. Existing spray evidence, manual priority, heartbeat timeout, and confirmed pump acknowledgements retained.
- [x] Coalesced ESP32 GPIO 26 buzzer pulses, mute cancellation, duty limits, and removal of duplicate automatic buzzer triggers.
- [x] Verifier failures disable automatic actuation.
- [x] Canonical v3 firmware supports correlated HTTP SMS status, retained-ID deduplication, boot IDs, bounded retries after definite queue rejection, and distinct queued/sent/failed/unknown outcomes.

## API and client

- [x] One authoritative paginated /api/logs route; legacy history/detections routes retained.
- [x] All ten extension endpoint families connected, including filters, false-positive marking, cooldowns, configuration, SMS, precision, and restricted snapshot serving. Added explicit correct-track review.
- [x] Missing Flutter API methods restored; scan polling uses /api/scan/state; request errors surface and failed mute changes roll back.
- [x] Shared monotonic scan timing with midpoint EWMA offset, restart/reconnection reset, serialized polling, and stale-response rejection.
- [x] Logs, snapshots, filtering, pagination, cooldowns, About, SMS test/status, and precision based on user markings.
- [x] Stone/charcoal/green styling, bundled typography, responsive layouts, and enlarged-text checks. Phone station tiles use one column with enlarged text.

## Branding and documentation

- [x] Leaf/scan-frame SVG logo, wordmark, favicon, and 32/180/192/512-pixel PNGs.
- [x] Registered assets; generated Android/iOS launcher icons and web icons.
- [x] Bundled Inter with its OFL license.
- [x] Starter README replaced; system overview covers architecture, 333-second formula, verifier gates, persistence, protocol, wiring, setup, and troubleshooting.
- [x] Existing databases and legacy history preserved. Historical summaries are not converted into fabricated track geometry.

## Verified results

- [x] **65 Python tests passed**, including existing pump/detection/repair/fungus regressions and new timing, attribution, verifier, persistence, snapshots, buzzer, SMS, and API tests.
- [x] All 13 frozen inference functions retain their baseline source hashes.
- [x] **24 Flutter tests passed**; Flutter analysis reports **no issues**.
- [x] Android debug APK built at build/app/outputs/flutter-apk/app-debug.apk.
- [x] Android release APK built at build/app/outputs/flutter-apk/app-release.apk (51.5 MB; versionCode 2).
- [x] Flutter web build and headless Edge interaction/viewport checks completed.
- [x] Canonical ESP32 firmware compiled with ESP32 core 3.3.11 for esp32:esp32:esp32; no flashing performed.
- [x] UI inspected at phone and tablet widths, with 1.0/1.5 text scales, disconnected state, empty logs, and a failed SMS command. Evidence is under .verification/ui and .verification/browser-*.png.
- [x] Pi/ESP32 acceptance procedure prepared in rpi_server/docs/HARDWARE_ACCEPTANCE.md.

## Pending connected-device acceptance

- [ ] Run the full physical scan cycle and verify motor/camera alignment.
- [ ] Verify actual Pi camera/Hailo inference, restart recovery, and persisted detections against representative targets.
- [ ] Verify pump release, manual priority, heartbeat loss, and relay acknowledgement on the connected rig.
- [ ] Verify GPIO 26 buzzer output, mute, coalescing, and duty limits.
- [ ] Verify SIM800L modem submission and actual handset receipt, including network loss/reboot cases.
- [ ] Install and exercise the debug APK on the intended Android device.

The iOS project and icons are present, but iOS compilation/signing requires macOS/Xcode and was not tested here. The local Android toolchain emits future-support deprecation warnings; its current debug build succeeds. Runtime logs and build evidence are in .verification (local, ignored). Hardware checks must remain pending until performed and recorded.

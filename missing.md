# FarmCam: What is Missing & Left To Do

> **Date:** October 15, 2026  
> **Target System:** FarmCam (Raspberry Pi 5 + Hailo-8L + ESP32 SIM800L + Flutter Client)  
> **Status:** Active Implementation — Modules, Coordinator, Extensions & UI Components Created; Wireup & Tests Pending

---

## Executive Summary

The core modular architecture for the overhaul has been completed:
- `rpi_server/scan_state.py`: 7-phase scan cycle state machine with transit/settle suppression.
- `rpi_server/verifier.py`: 5-gate detection verification filter (confidence, size/aspect ratio, skin rejection, whitefly green-ring, temporal drift).
- `rpi_server/persistence.py`: SQLite WAL persistence with rescan merge IoU tracking and `CooldownManager`.
- `rpi_server/buzzer.py`: Edge-triggered, coalescing buzzer scheduler with duty-cycle protections.
- `rpi_server/sms_broker.py`: Queue-based non-blocking SMS client targeting the ESP32 v3.0 firmware.
- `rpi_server/snapshot_writer.py`: Threaded background snapshot & thumbnail writer.
- `rpi_server/coordinator.py`: Master integration glue created to hook all modules cleanly into `main.py` without touching frozen inference functions.
- `rpi_server/api_extensions.py`: Full set of 10 extension API endpoints implemented.
- `lib/screens/about_screen.dart`: Complete system configuration viewer.
- `lib/screens/dashboard_screen.dart`: Buzzer mute toggle, About button, and CooldownChip indicators added.

**Current Primary Blocker:** The coordinator must be wired into `rpi_server/main.py` (at startup and in `detect_loop`), `api_service.dart` needs its missing helper methods, and brand assets, tests, and documentation remain to be written.

Below is the updated inventory of completed vs. remaining tasks across all subsystems.

---

## 1. Backend: Wiring in `rpi_server/main.py`

`coordinator.py` has been authored to wrap and coordinate all new modules while strictly preserving the frozen inference path (`postprocess_yolov8`, Hailo NMS decoding, etc.). The hook points into `main.py` are:

- [ ] **Startup Wireup:**
  - Call `coordinator.wire(app)` inside `startup()` (immediately after `init_db()`).
  - This initializes `PersistenceDB`, `CooldownManager`, `SnapshotWriter`, `SmsBroker`, `BuzzerScheduler`, `DetectionVerifier`, and `ScanStateMachine`, and registers all `api_extensions.py` endpoints on `app`.
- [ ] **State Machine & Timing Hook (`detect_loop`):**
  - Call `coordinator.tick()` once per loop iteration to step the state machine, maintain scan records, and synchronize legacy globals (`current_state`, `active_station_id`).
  - Flush the frame queue (`frame_queue.queue.clear()`) across phase transitions so stale transit frames are discarded.
  - Settle window gate (`SETTLE_SECS = 1.0s`): Ensure detection inference is bypassed during transit and settling phases.
- [ ] **Frame & Detection Hook (`detect_loop`):**
  - Call `coordinator.handle_frame(frame, detections, frame_seq)` directly downstream of `detect_frame()`.
  - Passes raw detections through the 5-gate verifier, persists confirmed tracks to SQLite WAL, enqueues snapshots, and edge-triggers actuators (buzzer & SMS).
- [ ] **Actuator Loop Cleanup:**
  - Replace legacy level-triggered buzzer polling loop in `apply_severity_actions()` with the edge-triggered `BuzzerScheduler`.

---

## 2. Backend: API Endpoints (`rpi_server/api_extensions.py`)

All 10 required endpoints are **fully authored** in `api_extensions.py`:

- [x] `GET /api/scan/state`: Authoritative 7-phase scan cycle state dict.
- [x] `GET /api/logs`: Paginated track list from `PersistenceDB` with multi-station and FP filters.
- [x] `POST /api/logs/{track_id}/false-positive`: Marks track as false positive for precision tracking.
- [x] `GET /api/cooldowns`: Real-time remaining/total seconds for pump and SMS actuators.
- [x] `GET /api/sms/status`: Returns last SMS dispatch and delivery status from `SmsBroker`.
- [x] `POST /api/sms/test`: Dispatches a test SMS to `?phone=` or default farmer phone.
- [x] `POST /api/buzzer/mute`: Mutes/unmutes buzzer scheduler via `{"muted": true|false}`.
- [x] `GET /api/config`: Exports non-secret configuration constants for Flutter About screen.
- [x] `GET /api/precision`: Calculates per-class precision metrics: `(total - FP) / total`.
- [x] `GET /api/snapshots/{filename}`: Safe image streaming from snapshot directories.
- [ ] **Remaining Action:** Expose live by executing `coordinator.wire(app)` in `main.py`.

---

## 3. Frontend: Flutter UI Components & Services (`lib/`)

- [x] **Cooldown Status UI:**
  - `lib/widgets/cooldown_chip.dart` mounted on `dashboard_screen.dart` (Pump & SMS cooldown badges with real-time countdown).
  - Cooldown polling loop integrated into `_startPolling()`.
- [x] **About / System Config Screen:**
  - `lib/screens/about_screen.dart` implemented with 6 grouped sections (Timing, Thresholds, Verifier Gates, Actuators, Severity, Hardware).
  - Navigation button with `Icons.info_outline_rounded` added to dashboard top bar.
- [x] **Buzzer Mute Button:**
  - Interactive mute/unmute button added to dashboard top bar with amber indicator state and optimistic UI update.
- [ ] **Missing API Service Methods (`lib/services/api_service.dart`):**
  - [ ] `Future<Map<String, dynamic>?> fetchConfig()`
  - [ ] `Future<bool> setBuzzerMute(bool muted)`
  - [ ] `Future<Map<String, dynamic>?> testSms([String? phone])`
  - [ ] `Future<Map<String, dynamic>?> fetchPrecision()`
- [ ] **Clock Drift Compensation (`scan_state_model.dart`):**
  - Implement clock-offset EWMA model to keep client-side remaining second timers accurately synced with server monotonic time.

---

## 4. Brand Assets: Logos & Icons (Component H)

The repository currently only has `assets/icon/farmcam_logo.png`. The following assets are still needed:

- [ ] `assets/logo.svg` (Leaf + scan frame design with dark/light mode `currentColor`)
- [ ] `assets/logo_wordmark.svg` (Horizontal branding logo)
- [ ] `assets/favicon.svg` (Simplified icon for web/browser tabs)
- [ ] Scaled PNG icons:
  - `assets/logo_32.png`
  - `assets/logo_180.png`
  - `assets/logo_192.png`
  - `assets/logo_512.png`
- [ ] Add asset directory declarations to `pubspec.yaml`.

---

## 5. Documentation: Technical Architecture Specification

- [x] `rpi_server/docs/AUDIT.md`: Pre-implementation codebase analysis and frozen path identification.
- [ ] `rpi_server/docs/SYSTEM_OVERVIEW.md`:
  - Complete architecture diagram (Mermaid flow).
  - 7-phase scan cycle timing formulas ($T_{\text{cycle}} = 325\text{ s}$).
  - 5-gate detection verifier mathematical criteria & formulas (skin rejection, green-ring context, temporal centroid drift).
  - Multi-station SQLite WAL schema & IoU rescan track merge logic.
  - ESP32 SIM800L hardware troubleshooting & UART2 wiring guide.

---

## 6. Test Suite: Automated Verification (`rpi_server/tests/`)

`rpi_server/tests/` directory is created but awaiting test files:

- [ ] `tests/test_scan_state.py`: 7-phase transition validation, simulated clock ticks, queue clearing, settle window suppression.
- [ ] `tests/test_verifier.py`: Synthetic test cases for confidence gate, aspect ratio, skin HSV detection, green ratio context, and centroid drift.
- [ ] `tests/test_persistence.py`: SQLite WAL operations, multi-station isolation (Station 2 scan does not purge Station 1), track updates, and FP toggles.
- [ ] `tests/test_cooldowns.py`: Actuator cooldown persistence across restarts.
- [ ] `tests/test_buzzer.py`: Edge-triggering, coalescing, and duty cycle limits.
- [ ] `tests/test_sms_protocol.py`: JSON ACK parsing, timeout retry backoff.

---

## Prioritized Implementation Roadmap

```
┌────────────────────────────────────────────────────────────────────────┐
│ COMPLETED                                                              │
│  ✔ Submodules (scan_state, verifier, persistence, buzzer, sms, snaps)   │
│  ✔ Integration glue: rpi_server/coordinator.py authored                 │
│  ✔ API extensions: rpi_server/api_extensions.py (all 10 endpoints)      │
│  ✔ Frontend widgets: CooldownChip row, Buzzer Mute, AboutScreen        │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STEP 1: Flutter ApiService Methods (Quick Fix)                         │
│  Add fetchConfig, setBuzzerMute, testSms, fetchPrecision to             │
│  lib/services/api_service.dart                                         │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STEP 2: Main.py Coordinator Wireup (Critical Backend Integration)      │
│  Connect coordinator.wire(app), coordinator.tick(), and                │
│  coordinator.handle_frame() in rpi_server/main.py                      │
│  (Strictly keeping inference pipeline functions frozen)               │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STEP 3: Brand Assets & pubspec.yaml (Component H)                      │
│  Generate SVGs (logo, wordmark, favicon) and scaled PNGs               │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STEP 4: Technical Documentation                                        │
│  Author rpi_server/docs/SYSTEM_OVERVIEW.md with Mermaid diagrams       │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ STEP 5: Pytest Test Suite                                              │
│  Implement unit & integration tests under rpi_server/tests/            │
└────────────────────────────────────────────────────────────────────────┘
```

---

> [!IMPORTANT]
> **Frozen Path Reminder:** When completing `main.py` wiring in Step 2, do NOT alter or reformat:
> `postprocess_yolov8`, `_decode_hailo_nms`, `_decode_nms_tensor`, `_dfl_decode`, `nms`, `_valid_box`, `_as_hwc`, `_maybe_sigmoid`, `_sigmoid`, `_softmax`, `run_inference_on_network`, `detect_frame`, or `_mask_to_polygon_and_area`.

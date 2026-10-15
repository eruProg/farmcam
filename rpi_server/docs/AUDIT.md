# FarmCam — Code Audit
*Read-only analysis. No edits made to any file during this phase.*

---

## 1. `main.py` — thread structure and data flow

### Threads and queues
| Thread | Function | Role |
|--------|----------|------|
| Main | `uvicorn.run(app)` | FastAPI HTTP server on port 8000 |
| `cam_thread` | `capture_frames()` | Reads 1280×960 lores stream from Picamera2 ~30 fps |
| `detect_thread` | `detect_loop()` | Reads `current_frame`, runs Hailo inference, updates dashboard |
| `viewer_thread` | `start_viewer_server()` | Second FastAPI instance on port 8080 |
| Ad-hoc daemons | `apply_severity_actions()`, `_pump_worker()`, `send_sms_alert()` | Spawned from detect_loop when thresholds are met |

There are **no queues** between capture and detection. Shared state:
- `current_frame` (numpy array) protected by `frame_lock`
- `frame_sequence` and `frame_timestamp` (monotonic)
- `latest_detections` (list) written by `detect_frame()`, read by `generate_mjpeg()` — no lock

### How frames reach Hailo
1. `capture_frames()` acquires `frame_lock`, copies `camera.capture_array("lores")` into `current_frame`, increments `frame_sequence`.
2. `detect_loop()` acquires `frame_lock`, copies `current_frame`, checks `sequence != last_frame_sequence` and `frame_age <= 2.0s`.
3. `detect_frame(frame_bgr)` → for each loaded HEF: crops frame for each tile region, calls `run_inference_on_network(crop, entry)`.
4. `run_inference_on_network()` resizes crop to model input size, converts BGR→RGB, calls `pipeline.infer()` via `InferVStreams`, passes results to `postprocess_yolov8()`.

### How results come back
- `postprocess_yolov8()` → `detect_frame()` → `temporal.update()` → `latest_detections` set as `draw_list` for MJPEG.
- `detect_loop()` reads returned `dets`, computes severity, calls `_publish_station(active_station_id, ...)` to update `stations_data[active_station_id]`.
- **Issue A1**: `active_station_id` is read at result-delivery time, not at frame-capture time.

---

## 2. Scan cycle — state machine and storage

### Current states (lines 1869–1910)
```
DETECT_A  →  PAUSE_5  →  DETECT_B  →  MOVING
  30 s          5 s          30 s       10/15 s
```

- `DETECT_A` = upper position, first 30 s window
- `PAUSE_5` = motor transit DOWN to lower position (no detection during this state)
- `DETECT_B` = lower position, second 30 s window
- `MOVING` = inter-station traverse (10 s stations 1-3, 15 s after station 4)

**Missing phase**: The motor must return UP before `MOVING`. This is currently implicit — the 5 s for return-to-upper is *not* a separate state. The `MOVING` timer implicitly includes it.

### Where state is stored
- `current_state: str` — global, one of `"DETECT_A"`, `"PAUSE_5"`, `"DETECT_B"`, `"MOVING"`, `"DETECTING"` (legacy)
- `active_station_id: int` — global (1–4)
- `cycle_index: int` — index into `lettuce_sequence = [1,2,3,4]`
- `state_start_time: float` — `time.time()` of when the current state began

**Issue A2**: `active_station_id` and `current_state` are both bare globals, unprotected by any lock. `detect_loop()` reads them after inference rather than tagging each frame with the state at capture time.

**Issue A3**: `detect_loop()` checks `infer_now = current_state in ("DETECT_A", "DETECT_B", "DETECTING")` but does *not* flush the frame queue on phase transitions. Old frames captured in `DETECT_B` can be processed after `MOVING` starts.

---

## 3. Detection storage, broadcast, and clearing

### In-memory
- `stations_data: dict[int, dict]` — per-station summary; overwritten on every detection frame.
- `latest_detections: list` — the raw draw-list for the MJPEG overlay, overwritten every frame.
- `recent_events: deque(maxlen=50)` — rolling event log, cleared on restart.
- `window_best: dict` — best-of-window severity, reset by `_reset_window_best()` on station change.

### Clearing behaviour
- `_reset_window_best()` is called in `_advance_station()` and when `MOVING → DETECT_A` transitions. This resets `temporal.buffer` (tracker) and `window_best`.
- **Issue C1**: Scanning station 2 calls `_advance_station()`, which resets `window_best` for station 1. Station 1's data in `stations_data` is **not** cleared, but it will be overwritten the next time station 1 is scanned — there is no persistence layer keeping the last confirmed detections across restarts.

### Database (`detection_logs.db`)
- `detection_logs` table: append-only log of every confirmed detection row.
- `lettuce_status` table: one row per lettuce, overwritten on every detection event.
- **Issue C2**: `lettuce_status` has no position column and no scan lifecycle tracking. It cannot support the "keep detections until a completed rescan" semantic.

### UI broadcast
- `/api/stations` → returns `list(stations_data.values())` — in-memory only.
- `/api/status` → same in-memory state.
- `/api/lettuce/{id}` → `_station_payload(id)` which reads `stations_data` then appends the last 25 DB log rows.
- No SSE or WebSocket; the Flutter app polls every 2 s.

---

## 4. Actuator trigger paths

### Pump
- `detect_loop()` → if `total_hits >= SPRAY_MIN_HITS` or severity == "high" and cooldown elapsed → spawns `threading.Thread(target=apply_severity_actions, ...)`.
- `apply_severity_actions()` → `_pump_worker(2.5, station, ...)` in a daemon thread.
- `_pump_worker()` → `esp32_cmd("PUMP_ON")` → wait `seconds` → `esp32_cmd("PUMP_OFF")`.
- Level-triggered: can fire multiple times per visit if cooldown (10 s) expires repeatedly.

### SMS
- Called from `_pump_worker()` after the pump stops (`send_sms_after=True`).
- Also called from `apply_severity_actions()` for severity SMS.
- Cooldown enforced via `sms_cooldown: dict` in memory — lost on restart.
- **Issue E1**: `send_sms_alert()` sends `esp32_cmd("SMS:" + text)`. ESP32 replies `OK:queued` (meaning "added to queue") not "delivered". No delivery ACK.

### Buzzer
- `apply_severity_actions()` calls `esp32_cmd_async("BUZZ_SPRAY")` after pump starts.
- On the Pi side, `init_buzzer()` sets up GPIO 17 but the buzzer is driven primarily from the ESP32 GPIO 26.
- **Which side drives the buzzer**: The ESP32 drives the physical buzzer (GPIO 26). The Pi's GPIO 17 is set up as a secondary path but `_buzzer_off()` sends both a GPIO LOW and an ESP32 `BUZZ_OFF` command.
- **Issue D1**: Buzzer is level-triggered — `apply_severity_actions()` can fire repeatedly if cooldown (10 s `PUMP_COOLDOWN`) is short enough.

---

## 5. Pi-to-ESP32 link

### Transport
- HTTP POST to `http://<ESP32_IP>:80/cmd` with `Content-Type: text/plain` body.
- ESP32 runs a `WebServer` on port 80.
- Function: `esp32_cmd(command, timeout)` in `main.py`.

### Message format
- **Pi → ESP32**: Plain text command string, e.g. `"PUMP_ON"`, `"SMS:FarmCam L1 HIGH: ..."`, `"BUZZ_ALERT"`.
- **ESP32 → Pi**: Plain text `"OK"` or `"OK:<detail>"` or `"ERR:<reason>"`.
- **No delivery ACK**: `"SMS:..."` returns `"OK:queued"` only, never a post-send confirmation.

### Firmware files
- `rpi_server/FarmCam_ESP32.ino` — primary deployed version (v2.2 + v2.3 additions, 789 lines)
- `rpi_server/farmcam_esp32/farmcam_esp32.ino` — development subfolder copy (older, 23 KB)

---

## 6. Frozen paths

The following must not be modified at any point during this project:

| Path | Description |
|------|-------------|
| `rpi_server/main.py` → `postprocess_yolov8()` | YOLOv8 detect/seg post-processing |
| `rpi_server/main.py` → `_decode_hailo_nms()` | Hailo native NMS decoder |
| `rpi_server/main.py` → `_decode_nms_tensor()` | Generic NMS tensor decoder |
| `rpi_server/main.py` → `_dfl_decode()` | Distribution Focal Loss decoding |
| `rpi_server/main.py` → `nms()` | IoU-based NMS |
| `rpi_server/main.py` → `_valid_box()` | Bounding box validation |
| `rpi_server/main.py` → `_as_hwc()` | Tensor format normaliser |
| `rpi_server/main.py` → `_maybe_sigmoid()` | Sigmoid for logit tensors |
| `rpi_server/main.py` → `_sigmoid()` | Element-wise sigmoid |
| `rpi_server/main.py` → `_softmax()` | Softmax helper |
| `rpi_server/main.py` → `run_inference_on_network()` | Single HEF inference runner |
| `rpi_server/main.py` → `detect_frame()` | Multi-HEF + tiled inference |
| `rpi_server/main.py` → `_mask_to_polygon_and_area()` | Segmentation mask decoder |
| `models HEF/*.hef` | All compiled HEF model files |
| `rpi_server/models/*.hef` | Alternate model storage location |
| `rpi_server/names_utf8.txt` | Class label map |

---

## 7. Root causes per task

| Task | Root cause |
|------|-----------|
| **A** (station bleed) | `active_station_id` read at result time, not frame-capture time. No frame-metadata tagging. No phase-transition queue flush. Detection runs during `PAUSE_5` transit and after `MOVING` starts. |
| **B** (false positives) | `TemporalConsensus` is the only filter. No size upper bound, no skin rejection, no context gate. A large skin-coloured region (finger) easily satisfies the loose whitefly IoU/centroid match. |
| **C** (persistence) | `stations_data` is in-memory only. No scan lifecycle in DB. Restart or reconnect clears everything. `lettuce_status` table has no position column and no "completed rescan" logic. |
| **D** (buzzer) | `apply_severity_actions()` is level-triggered with only a 10 s global cooldown. No per-class cooldown, no ON-time cap, no pattern coalescing, no edge-detection (fires for retained old detections too). |
| **E** (SMS) | `sendSMS()` in ESP32 blocks the loop for up to 70 s. Pi only gets `OK:queued` — no delivery ACK. No test endpoint. Phone numbers hardcoded in source. |

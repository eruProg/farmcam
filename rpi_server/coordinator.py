"""
coordinator.py — Integration glue for the FarmCam overhaul
===========================================================
This module wires every new subsystem (scan state machine, verifier,
persistence, buzzer, SMS broker, snapshot writer) into main.py WITHOUT
touching any frozen function.

main.py adds exactly two calls:
  1. At startup (after init_db):          coordinator.wire(app)
  2. In detect_loop, once per iteration:  coordinator.tick()
  3. In detect_loop, after detect_frame:  coordinator.handle_frame(frame, dets, seq)

The coordinator reads & writes the main.py globals it needs through the
``_main`` module reference (imported lazily so there are no circular deps
at module load time).  It NEVER alters any frozen function.

Frozen functions (NEVER imported, called, or patched here):
  postprocess_yolov8, _decode_hailo_nms, _decode_nms_tensor, _dfl_decode,
  nms, _valid_box, _as_hwc, _maybe_sigmoid, _sigmoid, _softmax,
  run_inference_on_network, detect_frame, _mask_to_polygon_and_area
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import config

log = logging.getLogger("farmcam.coordinator")

# ── Subsystem singletons (populated by wire()) ───────────────────────────────

_scan_sm:      Optional[Any] = None   # ScanStateMachine
_verifier:     Optional[Any] = None   # DetectionVerifier
_db:           Optional[Any] = None   # PersistenceDB
_cooldown_mgr: Optional[Any] = None   # CooldownManager
_buzzer:       Optional[Any] = None   # BuzzerScheduler
_sms_broker:   Optional[Any] = None   # SmsBroker
_snap_writer:  Optional[Any] = None   # SnapshotWriter

# Current open scan IDs — one per (station, position) while in progress
_open_scans: Dict[Tuple[int, str], str] = {}
_open_lock = threading.Lock()

# Track IDs confirmed this scan window (station -> set) so we don't double-fire actuators
_confirmed_new: Dict[int, set] = {}

_wired = False


# ── Public API ────────────────────────────────────────────────────────────────

def wire(app=None) -> None:
    """Initialise all subsystems and register API extensions.
    Call once from startup() after init_db()."""
    global _scan_sm, _verifier, _db, _cooldown_mgr, _buzzer, _sms_broker, _snap_writer, _wired

    if _wired:
        return
    _wired = True

    log.info("coordinator.wire(): initialising subsystems")

    # ── Persistence ──────────────────────────────────────────────────────
    try:
        from persistence import PersistenceDB, CooldownManager
        _db           = PersistenceDB(config.DB_PATH)
        _cooldown_mgr = CooldownManager(_db)
        log.info("  PersistenceDB ready: %s", config.DB_PATH)
    except Exception as exc:
        log.error("  PersistenceDB FAILED: %s", exc)

    # ── Snapshot writer ──────────────────────────────────────────────────
    try:
        from snapshot_writer import SnapshotWriter
        _snap_writer = SnapshotWriter(db=_db)
        _snap_writer.start()
        log.info("  SnapshotWriter started")
    except Exception as exc:
        log.error("  SnapshotWriter FAILED: %s", exc)

    # ── SMS broker ───────────────────────────────────────────────────────
    try:
        from sms_broker import SmsBroker
        _sms_broker = SmsBroker(
            esp32_ip   = config.ESP32_IP,
            esp32_port = config.ESP32_PORT,
        )
        _sms_broker.start()
        log.info("  SmsBroker started -> ESP32 at %s:%s", config.ESP32_IP, config.ESP32_PORT)
    except Exception as exc:
        log.error("  SmsBroker FAILED: %s", exc)

    # ── Buzzer scheduler ─────────────────────────────────────────────────
    try:
        from buzzer import BuzzerScheduler
        _buzzer = BuzzerScheduler()
        _buzzer.start()
        log.info("  BuzzerScheduler started (pin %d)", config.BUZZER_PIN)
    except Exception as exc:
        log.error("  BuzzerScheduler FAILED: %s", exc)

    # ── Detection verifier (lazy import — needs OpenCV) ───────────────────
    try:
        from verifier import DetectionVerifier
        _verifier = DetectionVerifier()
        log.info("  DetectionVerifier ready (5 gates)")
    except Exception as exc:
        log.error("  DetectionVerifier FAILED (will pass all detections): %s", exc)

    # ── Scan state machine ────────────────────────────────────────────────
    try:
        from scan_state import ScanStateMachine, ScanPhase
        _scan_sm = ScanStateMachine(stations=config.LETTUCE_SEQUENCE)
        _scan_sm.on_phase_change(_on_phase_change)
        _scan_sm.start()
        log.info("  ScanStateMachine started at station %d", _scan_sm.station)
    except Exception as exc:
        log.error("  ScanStateMachine FAILED: %s", exc)

    # ── API extension routes ──────────────────────────────────────────────
    if app is not None:
        try:
            from api_extensions import register_extensions
            register_extensions(app, get_shared_state)
            log.info("  API extensions registered")
        except Exception as exc:
            log.error("  API extensions FAILED: %s", exc)

    log.info("coordinator.wire(): done")


def get_shared_state() -> Dict[str, Any]:
    """Returns a snapshot dict for api_extensions and the Flutter UI."""
    import main as _main
    return {
        "scan_sm":          _scan_sm,
        "db":               _db,
        "cooldown_mgr":     _cooldown_mgr,
        "sms_broker":       _sms_broker,
        "buzzer_scheduler": _buzzer,
        # Legacy fallbacks (read-only)
        "active_station_id": _main.active_station_id,
        "current_state":     _main.current_state,
        "pump_active":       _main.pump_active,
        "pump_last_spray_time": _last_spray_time(),
    }


def tick() -> None:
    """Drive the ScanStateMachine.  Call once per detect_loop iteration BEFORE
    the frame-grab.  Syncs active_station_id / current_state back to main.py
    globals so legacy code continues to work."""
    if _scan_sm is None:
        return
    _scan_sm.tick()
    _sync_to_main()


def handle_frame(
    frame,
    raw_dets: List,
    seq: int,
) -> List:
    """
    Post-frozen-inference pipeline:
      1. Gate: skip frame if not in a detection phase (safety net).
      2. Build FrameMeta from the current scan state.
      3. Run DetectionVerifier on each candidate.
      4. Upsert verified tracks into PersistenceDB.
      5. Feed new tracks to BuzzerScheduler (edge-triggered).
      6. Submit frame to SnapshotWriter for thumbnails.

    Returns the list of VERIFIED detections (same format as raw_dets).
    This list is what main.py uses for dashboard publishing and live alerts.
    """
    if _scan_sm is None:
        # Subsystems not wired — return raw detections unchanged
        return raw_dets

    if not _scan_sm.is_detecting:
        return []

    meta = _scan_sm.make_frame_meta(seq)

    # ── Verifier ─────────────────────────────────────────────────────────
    if _verifier is not None:
        try:
            verified = _verifier.verify(raw_dets, frame, meta)
        except Exception as exc:
            log.error("verifier.verify raised: %s", exc)
            verified = raw_dets  # fail-open: don't block dashboard
    else:
        verified = raw_dets

    # ── Persistence ───────────────────────────────────────────────────────
    if _db is not None and verified:
        _persist_tracks(meta, verified, frame)

    # ── Snapshot (async, non-blocking) ────────────────────────────────────
    if _snap_writer is not None and verified:
        try:
            _snap_writer.submit(frame, verified, meta=meta)
        except Exception as exc:
            log.debug("snapshot submit: %s", exc)

    return verified


def shutdown() -> None:
    """Graceful teardown — call from cleanup()."""
    if _scan_sm is not None:
        try:
            _scan_sm.stop()
        except Exception:
            pass
    if _snap_writer is not None:
        try:
            _snap_writer.stop()
        except Exception:
            pass
    if _sms_broker is not None:
        try:
            _sms_broker.stop()
        except Exception:
            pass
    if _buzzer is not None:
        try:
            _buzzer.fail_safe_off()
            _buzzer.stop()
        except Exception:
            pass
    log.info("coordinator shutdown complete")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _on_phase_change(new_phase, station: int) -> None:
    """Callback fired synchronously by ScanStateMachine on every transition."""
    import main as _main
    from scan_state import ScanPhase

    # Flush queued frames — prevent stale frames from a previous phase being
    # processed in the new one.  main.py's detect_loop reads frame_sequence;
    # advancing it forces a re-grab.
    _main.frame_sequence += 1

    if new_phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER):
        # Start a new scan window in the DB
        pos = new_phase.position()
        if _db is not None:
            with _open_lock:
                scan_id = _db.begin_scan(station, pos)
                _open_scans[(station, pos)] = scan_id
        _confirmed_new[station] = set()
        log.info("Phase -> %s  station=%d  pos=%s", new_phase.name, station, pos)

    elif new_phase in (ScanPhase.TRANSIT_TO_LOWER, ScanPhase.TRANSIT_TO_UPPER,
                       ScanPhase.MOVING_TO_STATION, ScanPhase.SETTLING):
        log.info("Phase -> %s  station=%d", new_phase.name, station)


def _sync_to_main() -> None:
    """Mirror scan machine state back into main.py globals so legacy
    dashboard / MJPEG HUD code continues to work unchanged."""
    if _scan_sm is None:
        return
    try:
        import main as _main
        from scan_state import ScanPhase
        _main.active_station_id = _scan_sm.station
        # Map 7-phase enum -> 4-state legacy string for HUD
        _LEGACY_MAP = {
            ScanPhase.DETECT_UPPER:      "DETECT_A",
            ScanPhase.TRANSIT_TO_LOWER:  "PAUSE_5",
            ScanPhase.SETTLING:          "PAUSE_5",
            ScanPhase.DETECT_LOWER:      "DETECT_B",
            ScanPhase.TRANSIT_TO_UPPER:  "MOVING",
            ScanPhase.MOVING_TO_STATION: "MOVING",
            ScanPhase.IDLE:              "IDLE",
        }
        _main.current_state = _LEGACY_MAP.get(_scan_sm.phase, "IDLE")
    except Exception as exc:
        log.debug("_sync_to_main: %s", exc)


def _persist_tracks(meta, verified: List, frame) -> None:
    """Upsert confirmed detections into the DB and fire edge-triggered actuators."""
    try:
        from scan_state import ScanPhase
        # Only commit during actual detection phases
        if meta.phase not in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER):
            return

        with _open_lock:
            scan_id = _open_scans.get((meta.station, meta.position))
        if scan_id is None:
            return

        new_ids: List[str] = []
        for det in verified:
            try:
                x1, y1, x2, y2, name, conf = det[:6]
                result = _db.commit_scan(
                    scan_id, meta.station, meta.position,
                    [{
                        "class":      name,
                        "confidence": float(conf),
                        "bbox":       [int(x1), int(y1), int(x2), int(y2)],
                    }]
                )
                # commit_scan returns track_id string for new/updated tracks
                if isinstance(result, str):
                    confirmed_set = _confirmed_new.setdefault(meta.station, set())
                    if result not in confirmed_set:
                        confirmed_set.add(result)
                        new_ids.append((name, result))
            except Exception as exc:
                log.debug("_persist_tracks inner: %s", exc)

        # Edge-triggered actuators — fire ONLY for brand-new tracks
        for event_class, _ in new_ids:
            if _buzzer is not None:
                try:
                    _buzzer.trigger(event_class)
                except Exception as exc:
                    log.debug("buzzer.trigger: %s", exc)
    except Exception as exc:
        log.error("_persist_tracks: %s", exc)


def _last_spray_time() -> float:
    """Read last spray time from cooldowns table (fallback 0 if not wired)."""
    if _cooldown_mgr is None:
        return 0.0
    try:
        st = _cooldown_mgr.status("pump", config.PUMP_COOLDOWN_S)
        last = st.get("last_triggered_at") or 0.0
        return float(last)
    except Exception:
        return 0.0

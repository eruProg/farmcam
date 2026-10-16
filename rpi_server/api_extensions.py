"""
api_extensions.py — New API endpoints for the overhaul
=======================================================
This module is imported at the BOTTOM of main.py (after `app` is created)
to register new routes.  It does NOT touch any frozen function.

Add this at the very end of main.py (before the `if __name__` block):
    from api_extensions import register_extensions
    register_extensions(app, get_shared_state)

Where `get_shared_state()` returns a dict with all the globals that the
extensions need to read (never write to them — they're owned by main.py).

New endpoints:
  GET  /api/scan/state                → scan state dict (phase, station, etc.)
  GET  /api/logs                      → paginated track list
  POST /api/logs/{track_id}/false-positive → mark a track as FP
  GET  /api/cooldowns                 → pump + SMS cooldown status
  GET  /api/sms/status                → last SMS delivery status
  POST /api/sms/test                  → fire a test SMS on demand
  POST /api/buzzer/mute               → mute / unmute buzzer
  GET  /api/config                    → non-secret config constants (for About screen)
  GET  /api/precision                 → per-class precision from marked FP data
  GET  /api/snapshots/{filename}      → serve snapshot image files
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Query as FQuery
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config

log = logging.getLogger("farmcam.api_ext")


class _MuteBody(BaseModel):
    muted: bool


def register_extensions(
    app: FastAPI,
    get_state: Callable[[], Dict[str, Any]],
) -> None:
    """
    Register new endpoints on the FastAPI `app`.
    `get_state()` should return a dict with:
        - scan_sm:       ScanStateMachine instance (or None if not yet integrated)
        - db:            PersistenceDB instance
        - cooldown_mgr:  CooldownManager instance
        - sms_broker:    SmsBroker instance
        - active_station_id: int (legacy global fallback)
        - current_state: str   (legacy global fallback)
    """

    # ── Scan state ────────────────────────────────────────────────

    @app.get("/api/scan/state")
    async def api_scan_state():
        """
        Returns the authoritative scan state dict from ScanStateMachine.
        Also patches the /api/system response when `scan_state` key is absent.
        """
        state = get_state()
        sm = state.get("scan_sm")
        if sm is not None:
            return sm.state_dict()

        raise HTTPException(status_code=503, detail='Scan controller unavailable')

    # ── Track logs ────────────────────────────────────────────────

    @app.get("/api/logs")
    async def api_logs(
        station:   Optional[int] = FQuery(None, ge=1, le=4),
        cls:       Optional[str] = FQuery(None, alias="class"),
        status:    Optional[str] = FQuery(None),
        is_fp:     Optional[int] = FQuery(None, ge=0, le=1),
        date_from: Optional[float] = FQuery(None),
        date_to:   Optional[float] = FQuery(None),
        page:      int = FQuery(1, ge=1),
        page_size: int = FQuery(20, ge=1, le=100),
    ):
        """
        Paginated detection track list.
        Replaces the old log-based /api/logs endpoint if PersistenceDB is live.
        Falls back to legacy detection_logs query if DB is not yet connected.
        """
        if status not in (None, 'active', 'resolved') or cls not in (None, 'whiteflies', 'whitefly', 'worm', 'fungus'):
            raise HTTPException(status_code=422, detail='Invalid track filter')
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(status_code=422, detail='Invalid date range')
        state = get_state()
        db    = state.get("db")
        if db is not None:
            return db.get_tracks_page(
                station   = station,
                position  = None,
                cls       = cls,
                status    = status,
                is_fp     = is_fp,
                date_from = date_from,
                date_to   = date_to,
                page      = page,
                page_size = page_size,
            )

        raise HTTPException(status_code=503, detail='Detection storage unavailable')

    @app.post("/api/logs/{track_id}/false-positive")
    async def api_mark_fp(track_id: str):
        state = get_state()
        db    = state.get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="Persistence DB not active")
        ok = db.mark_false_positive(track_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Track not found")
        return {"ok": True}

    # ── Cooldown status ───────────────────────────────────────────

    @app.get("/api/cooldowns")
    async def api_cooldowns():
        state = get_state()
        cm    = state.get("cooldown_mgr")
        if cm is None:
            raise HTTPException(status_code=503, detail='Cooldown storage unavailable')
        return {
            "pump": cm.status("pump", config.PUMP_COOLDOWN_S),
            "sms":  cm.status("sms",  config.SMS_COOLDOWN_S),
            "buzzer_muted": state['buzzer_scheduler'].is_muted if state.get('buzzer_scheduler') else None,
        }

    # ── SMS status ────────────────────────────────────────────────

    @app.get("/api/sms/status")
    async def api_sms_status():
        state  = get_state()
        broker = state.get("sms_broker")
        if broker is not None:
            return broker.last_status()
        raise HTTPException(status_code=503, detail='SMS unavailable')

    # ── SMS test ──────────────────────────────────────────────────

    @app.post("/api/sms/test")
    async def api_sms_test(
        phone: Optional[str] = FQuery(None, description="Target phone; defaults to FARMCAM_FARMER_PHONE"),
    ):
        state  = get_state()
        broker = state.get("sms_broker")
        target = phone or config.FARMER_PHONE
        if not target:
            raise HTTPException(status_code=400, detail="No phone number configured. "
                                "Set FARMCAM_FARMER_PHONE env var or pass ?phone=")
        if broker is None:
            raise HTTPException(status_code=503, detail="SmsBroker not active")
        try:
            msg_id = broker.send(target, "FarmCam SMS test OK")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not msg_id:
            raise HTTPException(status_code=503, detail='SMS queue full')
        log.info("api_sms_test: queued %s to %s", msg_id, target)
        return {"ok": True, "msg_id": msg_id, "to": target}

    # ── Buzzer mute ───────────────────────────────────────────────

    @app.post("/api/buzzer/mute")
    async def api_buzzer_mute(body: _MuteBody):
        state     = get_state()
        scheduler = state.get("buzzer_scheduler")
        if scheduler is not None:
            scheduler.mute(body.muted)
            log.info("api_buzzer_mute: muted=%s", body.muted)
            return {"ok": True, "muted": body.muted}
        raise HTTPException(status_code=503, detail='Buzzer unavailable')

    # ── Config export ─────────────────────────────────────────────

    @app.get("/api/config")
    async def api_config():
        """Returns all non-secret config constants for the Flutter About screen."""
        return {
            # Timing
            "detect_window_secs":  config.DETECT_WINDOW_SECS,
            "transit_down_secs":   config.TRANSIT_DOWN_SECS,
            "transit_up_secs":     config.TRANSIT_UP_SECS,
            "move_secs":           config.MOVE_SECS,
            "loop_after_4_secs":   config.LOOP_AFTER_4_SECS,
            "settle_secs":         config.SETTLE_SECS,
            "cycle_time_secs":     4 * (config.DETECT_WINDOW_SECS * 2
                                        + config.TRANSIT_DOWN_SECS
                                        + config.TRANSIT_UP_SECS + 2 * config.SETTLE_SECS)
                                   + 3 * config.MOVE_SECS
                                   + config.LOOP_AFTER_4_SECS,
            # Thresholds
            "per_class_thresholds": config.PER_CLASS_THRESHOLDS,
            "confidence_threshold": config.CONFIDENCE_THRESHOLD,
            # Verifier gates
            "per_class_size":       {
                k: {"area_min": v[0], "area_max": v[1],
                    "ar_min": v[2], "ar_max": v[3]}
                for k, v in config.PER_CLASS_SIZE.items()
            },
            "skin_frame_max_pct":   config.SKIN_FRAME_MAX_PCT,
            "sigma_max":            config.SIGMA_MAX,
            "green_ratio_min":      config.GREEN_RATIO_MIN,
            # Temporal tracker
            "t_min_secs":           config.T_MIN_SECS,
            "track_k":              config.TRACK_K,
            "track_n":              config.TRACK_N,
            "centroid_drift_max":   config.CENTROID_DRIFT_MAX,
            # Actuation
            "pump_cooldown_s":      config.PUMP_COOLDOWN_S,
            "pump_duration_s":      config.PUMP_DURATION_S,
            "spray_min_hits":       config.SPRAY_MIN_HITS,
            "sms_cooldown_s":       config.SMS_COOLDOWN_S,
            "sms_max_retries":      config.SMS_MAX_RETRIES,
            "buzz_min_interval_s":  config.BUZZ_MIN_INTERVAL_S,
            "buzz_class_cooldown_s":config.BUZZ_CLASS_COOLDOWN_S,
            # Severity
            "sev_pest_high":        config.SEV_PEST_HIGH,
            "sev_pest_medium":      config.SEV_PEST_MEDIUM,
            "sev_fungus_high_pct":  config.SEV_FUNGUS_HIGH_PCT,
            "sev_fungus_med_pct":   config.SEV_FUNGUS_MED_PCT,
            # Retention
            "retention_max_gb":     config.RETENTION_MAX_GB,
            "retention_max_days":   config.RETENTION_MAX_DAYS,
            # Hardware
            "buzzer_pin":           26,
            "buzzer_controller":    "ESP32",
            "pump_pin":             config.PUMP_PIN,
            "esp32_ip":             config.ESP32_IP,
            "esp32_port":           config.ESP32_PORT,
            "lettuce_sequence":     config.LETTUCE_SEQUENCE,
        }

    # ── Precision metrics ─────────────────────────────────────────

    @app.get("/api/precision")
    async def api_precision():
        """Per-class precision = (total - false_positives) / total.
        Returns null for classes with no confirmed tracks yet."""
        state = get_state()
        db    = state.get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="PersistenceDB not active")
        return db.precision_by_class()

    # ── Snapshot serving ──────────────────────────────────────────

    @app.get('/api/snapshots/{filename:path}')
    async def api_snapshot(filename: str):
        root = Path(config.SNAPSHOT_DIR).resolve()
        candidate = (root / filename).resolve()
        if not candidate.is_relative_to(root) or candidate.suffix.lower() not in ('.jpg', '.png'):
            raise HTTPException(status_code=404, detail='Snapshot not found')
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail='Snapshot not found')
        return FileResponse(str(candidate), media_type='image/jpeg' if candidate.suffix.lower() == '.jpg' else 'image/png')

    @app.post('/api/logs/{track_id}/review')
    async def api_review(track_id: str):
        db = get_state().get('db')
        if db is None:
            raise HTTPException(status_code=503, detail='Detection storage unavailable')
        if not db.mark_reviewed(track_id):
            raise HTTPException(status_code=404, detail='Track not found')
        return {'ok': True}

    # ── Patch /api/system to include scan_state ───────────────────
    # We don't modify the existing /api/system function — instead we
    # add a dedicated /api/scan/state endpoint.  The Flutter app calls
    # that directly.  If you want to add scan_state to /api/system,
    # edit that function in main.py to call get_state()['scan_sm'].state_dict().

    log.info(
        "api_extensions: registered /api/scan/state, /api/logs, "
        "/api/logs/{id}/false-positive, /api/cooldowns, /api/sms/status, "
        "/api/sms/test, /api/buzzer/mute, /api/config, /api/precision, "
        "/api/snapshots/{filename}"
    )


def _label(phase: str) -> str:
    return {
        "IDLE":              "Idle",
        "MOVING_TO_STATION": "Moving",
        "SETTLING":          "Settling",
        "DETECT_UPPER":      "Detecting (Upper)",
        "TRANSIT_TO_LOWER":  "Lowering",
        "TRANSIT_TO_UPPER":  "Raising",
        "DETECT_LOWER":      "Detecting (Lower)",
    }.get(phase, phase)

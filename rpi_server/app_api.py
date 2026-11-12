"""
app_api.py — endpoints for the redesigned phone app
====================================================
Registered from main.py after api_extensions. Reads state only through the
`runtime` object (main.py's module, or a stand-in in tests):

  GET    /api/hello                  identify a FarmCam Pi (app auto-discovery)
  GET    /api/app/state              everything the app shows, in ONE call per poll
  GET    /api/captures               dataset photos, newest first (paged)
  GET    /api/captures/{name}        full-resolution photo
  GET    /api/captures/{name}/thumb  400 px preview
  DELETE /api/captures/{name}        remove a photo
  POST   /api/capture/burst          take N photos in the background
  GET    /api/reports/summary        per-day / per-station counts for History
  GET    /api/reports/export.csv     every detection track as CSV
  GET    /api/events?after=<id>      new notification events (after=-1: newest id only)
  POST   /api/events/test            create a test notification event
  POST   /api/system/power           {"action": "shutdown"|"restart"}: stop scan + pump, then power

runtime must provide: SERVER_BUILD, MODEL_COLOR_ORDER, start_time,
station_payloads(), pump_state(), esp32_state(), health_state(),
capture_state(), start_burst(count, interval_s, station, position, note);
optional: live_state(), activity_state(scan).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Query as FQuery
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import config

CAPTURE_NAME = re.compile(r"^field_full_[0-9_]+\.jpg$")
STATION_FIELDS = (
    "id", "name", "status", "label", "health", "confidence", "last_check",
    "pest_count", "fungus_count", "pest_severity_level", "fungus_severity_level",
    "fungus_coverage_pct", "pest_types", "infestation_desc", "active",
    "whitefly_count", "worm_count",
)
CSV_FIELDS = ("id", "station", "position", "class", "confidence", "first_seen", "last_seen",
              "scans_seen", "status", "is_fp", "reviewed", "coverage_pct", "snapshot_path")


POWER_DELAY_S = 3        # lets the reply reach the phone first
SYSTEMCTL = "/usr/bin/systemctl"
# Tried in order. install_update.sh lets a non-root service user run exactly
# these through sudo (/etc/sudoers.d/farmcam-power).
POWER_COMMANDS = {"shutdown": [[SYSTEMCTL, "poweroff"], ["/usr/sbin/shutdown", "-h", "now"]],
                  "restart": [[SYSTEMCTL, "reboot"], ["/usr/sbin/shutdown", "-r", "now"]]}
POWER_TEXT = {"shutdown": ("FarmCam shutting down", "The Pi was shut down from the app."),
              "restart": ("FarmCam restarting", "The Pi is restarting; back in about 1-2 minutes.")}
POWER_FAILED = {"shutdown": "Error: Could not shut down the Pi", "restart": "Error: Could not restart the Pi"}
NO_POWER_PERMISSION = ("FarmCam has no permission to shut down or restart the Pi. "
                       "Run install_update.sh again on the Pi.")


class PowerBody(BaseModel):
    action: str = Field(..., pattern="^(shutdown|restart)$")


def _is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _power_commands(action):
    """Root runs the commands directly; any other service user through
    `sudo -n` (never waits for a password)."""
    prefix = [] if _is_root() else ["sudo", "-n"]
    return [prefix + command for command in POWER_COMMANDS[action]]


def _power_allowed(action):
    """Checked BEFORE stopping anything: a refused `systemctl poweroff` used to
    stop the scan and then leave the Pi running without a word."""
    if _is_root():
        return True
    verb = "poweroff" if action == "shutdown" else "reboot"
    try:
        return subprocess.run(["sudo", "-n", "-l", SYSTEMCTL, verb], capture_output=True,
                              timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run_power_command(command):
    """(ok, message); the message is what the command printed on failure."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _power_off(action, runtime, db=None):
    """Stop the scan and the pump, then shut down or restart the Pi. On the
    PC (Windows or FARMCAM_DEV) the command is only logged. If every command
    fails, the phone gets an error event with the reason."""
    time.sleep(POWER_DELAY_S)
    try:
        import coordinator
        coordinator.stop_scan()
    except Exception:
        pass
    try:
        if runtime is not None and hasattr(runtime, "esp32_cmd"):
            runtime.esp32_cmd("PUMP_OFF", timeout=1.5, retries=1)
    except Exception:
        pass
    commands = _power_commands(action)
    if os.name == "nt" or getattr(runtime, "DEV_MODE", False):
        print(f"[power] dev mode: would run {' '.join(commands[0])}")
        return
    errors = []
    for command in commands:
        print(f"[power] {action} requested from the app: {' '.join(command)}")
        ok, message = _run_power_command(command)
        if ok:
            return
        print(f"[power] {' '.join(command)} failed: {message}")
        errors.append(message)
    if db is not None:
        try:
            db.add_event("error", POWER_FAILED[action], "; ".join(errors)[:300])
        except Exception:
            pass


class BurstBody(BaseModel):
    count: int = Field(5, ge=1, le=30)
    interval_s: float = Field(2.0, ge=1.0, le=30.0)
    station: Optional[int] = Field(None, ge=1, le=4)
    position: Optional[str] = Field(None, pattern="^(upper|lower)$")
    note: str = Field("", max_length=80)


def cooldowns_payload(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pump/SMS cooldowns + buzzer mute; shared by /api/cooldowns,
    /api/system and /api/app/state. None when storage is not wired."""
    cm = state.get("cooldown_mgr")
    if cm is None:
        return None
    buzzer = state.get("buzzer_scheduler")
    return {
        "pump": cm.status("pump", config.PUMP_COOLDOWN_S),
        "sms": cm.status("sms", config.SMS_COOLDOWN_S),
        "buzzer_muted": buzzer.is_muted if buzzer else None,
    }


def _relative_snapshot(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(Path(config.SNAPSHOT_DIR).resolve()).as_posix()
    except ValueError:
        return None


def _capture_dir() -> Path:
    return Path(config.CAPTURE_DIR)


def _capture_meta(path: Path) -> Dict[str, Any]:
    meta = {"name": path.name, "size_bytes": path.stat().st_size,
            "time": path.stat().st_mtime, "station": None, "position": None, "note": ""}
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        try:
            meta.update(json.loads(sidecar.read_text()))
        except (OSError, ValueError):
            pass
    meta["has_thumb"] = (path.parent / "thumbs" / path.name).exists()
    return meta


def _capture_path(name: str) -> Path:
    if not CAPTURE_NAME.match(name):
        raise HTTPException(status_code=404, detail="Photo not found")
    path = _capture_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")
    return path


def build_app_state(state: Dict[str, Any], runtime) -> Dict[str, Any]:
    db = state.get("db")
    summary = {row["station_id"]: row for row in db.get_station_summary()} if db else {}
    stations = []
    for payload in runtime.station_payloads():
        entry = {k: payload.get(k) for k in STATION_FIELDS}
        row = summary.get(payload.get("id"), {})
        entry["threats"] = [dict(d, thumb_path=_relative_snapshot(d.get("thumb_path")))
                            for d in row.get("detections", [])]
        entry["last_scanned"] = row.get("last_scanned")
        entry["severity"] = row.get("severity", "clear")
        stations.append(entry)
    body = {
        "build": runtime.SERVER_BUILD,
        "model_colors": runtime.MODEL_COLOR_ORDER,
        "uptime_s": int(time.time() - runtime.start_time),
        "scan": state.get("scan_state"),
        "stations": stations,
        "cooldowns": cooldowns_payload(state),
        "pump": runtime.pump_state(),
        "esp32": runtime.esp32_state(),
        "health": dict(runtime.health_state(), fault=state.get("fault"), ready=bool(state.get("ready"))),
        "capture": runtime.capture_state(),
        "recent": db.recent_tracks(5) if db else [],
        # Confirmed during the current lettuce visit, before the window ends.
        "live": runtime.live_state() if hasattr(runtime, "live_state") else None,
        # Spraying / moving to a lettuce: the app's full-screen pop-up.
        "activity": (runtime.activity_state(scan=state.get("scan_state"))
                     if hasattr(runtime, "activity_state") else None),
    }
    body["rev"] = state_rev(body)
    return body


def state_rev(body: Dict[str, Any]) -> str:
    """Changes only when something the app shows changes. Countdown values
    (remaining_s, server_time) are left out: the app ticks those locally."""
    scan = body.get("scan") or {}
    cooldowns = body.get("cooldowns") or {}
    stable = {
        "scan": {k: scan.get(k) for k in ("run_id", "running", "phase", "station", "position", "scan_id")},
        "stations": body.get("stations"),
        "cooldowns": {k: (v or {}).get("last_triggered_at") if isinstance(v, dict) else v
                      for k, v in cooldowns.items()},
        "pump": (body.get("pump") or {}).get("active"),
        "esp32": {k: (body.get("esp32") or {}).get(k) for k in ("ip", "reachable", "scanning")},
        "health": {k: v for k, v in (body.get("health") or {}).items() if not k.endswith("_age_s")},
        "capture": body.get("capture"),
        "recent": [(t.get("id"), t.get("scans_seen"), t.get("status")) for t in body.get("recent") or []],
        "live": {k: (body.get("live") or {}).get(k)
                 for k in ("station", "position", "counts", "fungus_pct", "spray_reason")},
        "activity": {k: (body.get("activity") or {}).get(k) for k in ("kind", "title")},
    }
    return hashlib.sha1(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:12]


def register_app_api(app: FastAPI, get_state: Callable[[], Dict[str, Any]], runtime) -> None:

    @app.get("/api/hello")
    def api_hello():
        return {"app": "farmcam", "build": runtime.SERVER_BUILD,
                "hostname": socket.gethostname(), "port": 8000}

    @app.get("/api/app/state")
    def api_app_state():
        return build_app_state(get_state(), runtime)

    # ── Dataset captures ──────────────────────────────────────────

    @app.get("/api/captures")
    def api_captures(page: int = FQuery(1, ge=1), page_size: int = FQuery(30, ge=1, le=100)):
        folder = _capture_dir()
        files = sorted((p for p in folder.glob("field_full_*.jpg")), key=lambda p: p.name, reverse=True) \
            if folder.exists() else []
        start = (page - 1) * page_size
        return {"items": [_capture_meta(p) for p in files[start:start + page_size]],
                "total": len(files), "page": page, "page_size": page_size,
                "pages": max(1, -(-len(files) // page_size))}

    @app.get("/api/captures/{name}")
    def api_capture_file(name: str):
        return FileResponse(str(_capture_path(name)), media_type="image/jpeg")

    @app.get("/api/captures/{name}/thumb")
    def api_capture_thumb(name: str):
        path = _capture_path(name)
        thumb = path.parent / "thumbs" / name
        return FileResponse(str(thumb if thumb.exists() else path), media_type="image/jpeg")

    @app.delete("/api/captures/{name}")
    def api_capture_delete(name: str):
        path = _capture_path(name)
        for extra in (path.with_suffix(".json"), path.parent / "thumbs" / name):
            extra.unlink(missing_ok=True)
        path.unlink()
        return {"ok": True}

    @app.post("/api/capture/burst")
    def api_capture_burst(body: BurstBody):
        try:
            runtime.start_burst(body.count, body.interval_s, body.station, body.position, body.note)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"ok": True, "capture": runtime.capture_state()}

    # ── Reports ───────────────────────────────────────────────────

    @app.get("/api/reports/summary")
    def api_report_summary(days: int = FQuery(14, ge=1, le=90)):
        db = get_state().get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="Detection storage unavailable")
        return db.report_summary(days)

    @app.get("/api/reports/export.csv")
    def api_report_csv():
        db = get_state().get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="Detection storage unavailable")
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in db.export_tracks():
            for key in ("first_seen", "last_seen"):
                if row.get(key):
                    row[key] = datetime.fromtimestamp(row[key]).isoformat(timespec="seconds")
            writer.writerow(row)
        name = f"farmcam_detections_{datetime.now():%Y%m%d}.csv"
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ── Phone notifications ───────────────────────────────────────

    @app.get("/api/events")
    def api_events(after: int = FQuery(-1, ge=-1), limit: int = FQuery(20, ge=1, le=100)):
        db = get_state().get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="Event storage unavailable")
        events, last_id = db.events_after(after, limit)
        return {"events": events, "last_id": last_id}

    @app.post("/api/events/test")
    def api_events_test():
        db = get_state().get("db")
        if db is None:
            raise HTTPException(status_code=503, detail="Event storage unavailable")
        event_id = db.add_event("test", "Test notification",
                                "FarmCam alerts work on this phone: whitefly, worm, fungus, sprays and errors.")
        return {"ok": True, "id": event_id}

    # ── Power ─────────────────────────────────────────────────────

    @app.post("/api/system/power")
    def api_system_power(body: PowerBody):
        dev = os.name == "nt" or getattr(runtime, "DEV_MODE", False)
        if not dev and not _power_allowed(body.action):
            raise HTTPException(status_code=503, detail=NO_POWER_PERMISSION)
        title, text = POWER_TEXT[body.action]
        db = get_state().get("db")
        if db is not None:
            try:
                db.add_event("system", title, text)
            except Exception:
                pass
        threading.Thread(target=_power_off, args=(body.action, runtime, db), daemon=True,
                         name="farmcam_power").start()
        return {"ok": True, "action": body.action, "in_s": POWER_DELAY_S}

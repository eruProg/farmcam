"""
FarmCam - RPi5 Server (FIXED Dual-Hailo Version)
================================================
Fixes applied:
1. Single shared VDevice for both models (critical Hailo limitation)
2. Proper network group activation handling
3. Prefer local models/ folder first
4. Better logging so you can see exactly what fails
5. Safer input format handling (UINT8 preferred)

SETUP (one time on RPi5):
    sudo apt update && sudo apt install -y python3-pip python3-venv
    python3 -m venv farmcam_env
    source farmcam_env/bin/activate
    pip install fastapi uvicorn picamera2 ultralytics opencv-python-headless RPi.GPIO pyserial

RUN:
    ./farmcam_env/bin/python3 main.py
"""

import json
import subprocess
import time
import threading
import os
from datetime import datetime
from pathlib import Path
from collections import deque
import sqlite3
import traceback

import cv2
import numpy as np
from fastapi import FastAPI, Response, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# New subsystem coordinator (non-frozen; wires after startup)
import coordinator

# ---------------------------------------------------------------------------
# Optional hardware imports
# ---------------------------------------------------------------------------
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    print("⚠  pyserial not found — Arduino buzzer disabled")

try:
    from picamera2 import Picamera2
    HAS_CAMERA = True
except ImportError:
    HAS_CAMERA = False
    print("⚠  picamera2 not found — camera detection unavailable")

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    print("⚠  RPi.GPIO not found — buzzer disabled")

try:
    from hailo_platform import (
        HEF, VDevice, HailoStreamInterface,
        InferVStreams, ConfigureParams,
        InputVStreamParams, OutputVStreamParams,
        FormatType
    )
    HAS_HAILO = True
except ImportError:
    HAS_HAILO = False
    print("⚠  hailo_platform not found — detection disabled")

try:
    from ultralytics import YOLO
    HAS_ULTRALYTICS = True
except ImportError:
    HAS_ULTRALYTICS = False
    print("⚠  ultralytics not found — PyTorch fallback disabled")

# ---------------------------------------------------------------------------
# Camera Profiles
# ---------------------------------------------------------------------------
CAMERA_PROFILES = {
    "auto_default": {
        "label": "📷 Auto Default",
        "description": "True-color baseline with auto exposure & white balance.",
        "model_type": "color",
        "controls": {
            "AeEnable": True,
            "AwbEnable": True,
            "Sharpness": 1.0,
            "Contrast": 1.0,
            "Saturation": 1.0,
        },
    },
    "warm_tone": {
        "label": "🔥 Warm Tone",
        "description": "Enhanced warm color shifts for fungus & disease detection.",
        "model_type": "color",
        "controls": {
            "AeEnable": True,
            "AwbEnable": False,
            "ColourGains": (1.8, 1.2),
            "Sharpness": 1.5,
            "Contrast": 1.2,
            "Saturation": 1.3,
        },
    },
    "grayscale_edge": {
        "label": "🏁 Grayscale Edge",
        "description": "High contrast shape-based detection for whiteflies & worms.",
        "model_type": "grayscale",
        "controls": {
            "AeEnable": True,
            "AwbEnable": True,
            "Sharpness": 2.0,
            "Contrast": 1.5,
            "Saturation": 0.0,
        },
    },
}

PROFILE_NAMES = list(CAMERA_PROFILES.keys())
active_profile = "auto_default"
active_model_type = "color"

# ---------------------------------------------------------------------------
# Dataset / Model paths
# ---------------------------------------------------------------------------
def get_dataset_base_path():
    """Find SSD or fall back to home."""
    for root_dir in [Path("/media"), Path("/mnt")]:
        if root_dir.exists():
            try:
                for user_dir in sorted(root_dir.iterdir()):
                    if user_dir.is_dir():
                        candidates = [user_dir] + (list(user_dir.iterdir()) if not os.path.ismount(str(user_dir)) else [])
                        for path in candidates:
                            if path.is_dir() and os.path.ismount(str(path)):
                                res = path / "New Datasets"
                                res.mkdir(parents=True, exist_ok=True)
                                print(f"💾 SSD detected at: {res}")
                                return res
            except Exception:
                pass
    fallback = Path.home() / "New Datasets"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

DATASET_BASE = get_dataset_base_path()
print(f"📁 DATASET_BASE: {DATASET_BASE}")

# Prefer local models/ next to this script (as shown in your screenshot)
SCRIPT_DIR = Path(__file__).parent.resolve()
LOCAL_MODELS_DIR = SCRIPT_DIR / "models"

# Create profile folders
try:
    DATASET_BASE.mkdir(parents=True, exist_ok=True)
    for profile_name in PROFILE_NAMES:
        (DATASET_BASE / profile_name).mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"⚠ Could not create dataset folders: {e}")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SERVER_PORT = 8000
SERVER_BUILD = "2026-09-26-small-targets-2"
VIEWER_PORT = 8080
BUZZER_PIN = 17
PUMP_PIN = 27
PUMP_DURATION = 3.0

ESP32_IP = os.environ.get("FARMCAM_ESP32_IP", "10.244.166.161")
ESP32_PORT = 80
FARMER_PHONE = "639916425163"    # farmer handset, no + (SIM800L)
SIM800L_PHONE = "639693141995"   # SIM inside SIM800L (sender)

PEST_HIGH_THRESHOLD = 3
PEST_MED_THRESHOLD = 1
FUNGUS_HIGH_COV_PCT = 20.0
FUNGUS_MED_COV_PCT = 5.0

PUMP_HIGH_SECS = 2.5
PUMP_MED_SECS = 2.5
PUMP_LOW_SECS = 2.5
PUMP_CYCLE_ON_SECS = 2.5
SPRAY_MIN_HITS = 2
PUMP_CYCLE_COUNT = 1
PUMP_CYCLE_GAP_SECS = 2.0
PUMP_COOLDOWN = 10  # seconds after buzz+pump+sms

# Station cycle: 30s detect → 5s pause → 30s detect → 10s move
# After lettuce 4: 15s then back to lettuce 1
DETECT_WINDOW_SECS = 30.0
PAUSE_SECS = 5.0
MOVE_SECS = 10.0
LOOP_AFTER_4_SECS = 15.0

LAST_PUMP_FILE = str(SCRIPT_DIR / "last_pump_time.txt")
SMS_COOLDOWN_SECS = 10

# Dual model class names
SEG_CLASS_NAMES = ["fungus"]
DETECT_CLASS_NAMES = ["whiteflies", "worm"]
CLASS_NAMES = ["fungus", "whiteflies", "worm"]
NUM_CLASSES = 3

# How HEFs share the Hailo chip (only one network really fits well with best.hef)
#   "split"     — load ONLY best_seg.hef + best_detect.hef (skip best.hef)
#   "combined"  — load ONLY best.hef
#   "rotate"    — one HEF at a time: DETECT_A=DET, DETECT_B=SEG, next station=COMB
#   "all"       — try to keep all 3 configured (usually fails after best.hef)
HAILO_MODE = "split"    # both SEG + DET loaded on chip simultaneously

TARGET_SSID = "FarmCam"
TARGET_PASS = "farmcam1"
DETECTION_INTERVAL = 0.10
# Full frame plus four overlapping crops, merged before temporal confirmation.
# Disable on slower hardware with FARMCAM_SMALL_OBJECT_TILES=0.
SMALL_OBJECT_TILES = os.environ.get("FARMCAM_SMALL_OBJECT_TILES", "1") == "1"
CONFIDENCE_THRESHOLD = 0.35          # fallback for classes without an override
NMS_IOU_THRESHOLD = 0.45
REG_MAX = 16
MIN_BOX_AREA = 150                   # was 8 (~3x3px) — let single-pixel noise register as a "pest"
# Model-space pixels; small insects/lesions must not inherit the worm size gate.
# Starting values to validate on real camera images, not measured accuracy claims.
PER_CLASS_MIN_BOX_AREA = {"whiteflies": 16, "whitefly": 16, "fungus": 25}
MIN_MASK_AREA_PX = 4                 # proto pixels (4 pixels = 64 image pixels at 640/160)
MIN_MASK_FILL_RATIO = 0.05           # total mask pixels / box-crop pixels — rejects near-empty/speckle masks
MAX_DETECTIONS_PER_FRAME = 30
MIN_ASPECT_RATIO = 0.15              # was 0.08 — was letting 1px-tall slivers through as "worm"
MAX_ASPECT_RATIO = 8.0               # was 12.0

PER_CLASS_THRESHOLDS = {
    # Fungus is a SEGMENTATION class over a diffuse, mottled texture — its
    # confidence naturally calibrates lower than a crisp insect detection
    # even for a large, obvious infection. 0.45 (tried previously) rejected
    # real widespread fungus almost entirely; 0.28 relies on MIN_BOX_AREA,
    # the mask-quality gate, and temporal consensus to keep out noise
    # instead of leaning on confidence alone.
    "fungus": 0.28,
    "whitefly": 0.40,
    "whiteflies": 0.40,
    "worm": 0.45,
}

SEV_PEST_HIGH = 3
SEV_PEST_MEDIUM = 1
# BUG FIX: these used to be 9999.0 — an unreachable threshold — so fungus
# severity could only ever be "low" or "none", no matter how much of the
# leaf was covered. Real percentage-of-leaf-coverage tiers now apply.
# Tune these to whatever your agronomy calls "spreading" vs "severe".
SEV_FUNGUS_MEDIUM_PCT = 5.0
SEV_FUNGUS_HIGH_PCT = 15.0

MIN_CONF_THRESHOLD = min(PER_CLASS_THRESHOLDS.values())

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
app = FastAPI(title="FarmCam API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

camera = None
current_frame = None
frame_sequence = 0
frame_timestamp = 0.0
stop_event = threading.Event()
worker_threads = []
instance_lock_file = None
frame_lock = threading.Lock()

active_station_id = 1
lettuce_sequence = [1, 2, 3, 4]
cycle_index = 0
STATION_DETECT_TIME = DETECT_WINDOW_SECS
STATION_MOVE_TIME = MOVE_SECS
LOG_INTERVAL = 1.0
current_state = "DETECT_A"
state_start_time = time.time()
window_best = {"pest_severity": "none", "fungus_severity": "none", "severity": "none",
               "desc": "", "pest": 0, "fungus": 0.0, "types": []}
# Peak concurrent hits (pest+fungus boxes in one single frame) and the
# running sum of per-frame hits seen so far during the CURRENT dwell at a
# station. These feed the end-of-visit spray decision — see _reset_window_best.
_dwell_hits_peak = 0
_dwell_sightings = 0

stations_data = {
    i: {
        "id": i,
        "name": f"Lettuce {i}",
        "status": "clear",
        "confidence": 0.0,
        "last_check": datetime.now().isoformat(),
        "pest_count": 0,
        "fungus_count": 0,
        "severity_level": "none",
        "pest_severity_level": "none",
        "fungus_severity_level": "none",
        "fungus_coverage_pct": 0.0,
        "pest_types": [],
        "infestation_desc": "",
        "whitefly_count": 0,
        "worm_count": 0,
        "detections": [],
    }
    for i in range(1, 5)
}

buzzer_active = False
buzzer_muted = False
arduino_serial = None
start_time = time.time()
latest_detections = []
recent_events = deque(maxlen=50)
pt_model = None

pump_active = False
pump_lock = threading.Lock()
sms_cooldown = {}
sms_lock = threading.Lock()

# Manual pump hold state (app hold-to-spray button)
manual_pump_on = False
manual_pump_heartbeat = 0.0     # last heartbeat timestamp
MANUAL_PUMP_TIMEOUT = 2.0      # auto-off if no heartbeat in this many seconds
manual_control_lock = threading.Lock()
manual_pump_generation = 0

# ---------------------------------------------------------------------------
# Hailo multi-model state (SHARED VDevice)
# ---------------------------------------------------------------------------
hailo_vdevice = None
hailo_models = []   # currently configured HEFs
hailo_catalog = []  # [(label, path, classes), ...] discovered on disk
hailo_swap_lock = threading.Lock()
seg_model_loaded = False
det_model_loaded = False
comb_model_loaded = False
model = None

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = str(SCRIPT_DIR / "detection_logs.db")

def init_db():
    print(f"🗄  Database: {DB_PATH}")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detection_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lettuce_id INTEGER NOT NULL,
                pest_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                model_used TEXT,
                camera_profile TEXT,
                detected_at TEXT NOT NULL,
                severity_level TEXT DEFAULT 'low',
                fungus_coverage_pct REAL DEFAULT 0.0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lettuce_status (
                lettuce_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'clear',
                pest_count INTEGER DEFAULT 0,
                fungus_count INTEGER DEFAULT 0,
                last_check TEXT,
                severity_level TEXT DEFAULT 'none',
                fungus_coverage_pct REAL DEFAULT 0.0,
                pest_types TEXT DEFAULT '',
                infestation_desc TEXT DEFAULT ''
            )
        """)
        # migrations
        for table, cols in [
            ("detection_logs", [("severity_level", "TEXT", "'low'"), ("fungus_coverage_pct", "REAL", "0.0")]),
            ("lettuce_status", [
                ("pest_count", "INTEGER", "0"), ("fungus_count", "INTEGER", "0"),
                ("severity_level", "TEXT", "'none'"), ("fungus_coverage_pct", "REAL", "0.0"),
                ("pest_types", "TEXT", "''"), ("infestation_desc", "TEXT", "''"),
            ]),
        ]:
            existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            for col, typ, default in cols:
                if col not in existing:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ} DEFAULT {default}")
                        print(f"🗄  Migrated {table}: added {col}")
                    except Exception as e:
                        print(f"Migration error: {e}")
        for i in range(1, 5):
            conn.execute("INSERT OR IGNORE INTO lettuce_status (lettuce_id) VALUES (?)", (i,))
        conn.commit()
    print("✅ Database ready")

def compute_severity_info(pest_count, fungus_coverage_pct, pest_types_detected):
    """Pest (whiteflies/worm) and fungus are two different problems with two
    different treatments — a real infestation of one must never be hidden
    or overwritten by a reading of the other. Returns
    (pest_severity, fungus_severity, desc) as two INDEPENDENT severities,
    not one merged "worst of both" value."""
    parts = []
    types_str = " & ".join(pest_types_detected) if pest_types_detected else "Pest"

    if pest_count >= SEV_PEST_HIGH:
        pest_sev = "high"
        parts.append(f"{types_str} infestation detected — {pest_count} insects found (HIGH)")
    elif pest_count >= SEV_PEST_MEDIUM:
        pest_sev = "medium"
        parts.append(f"{types_str} detected — {pest_count} insect(s) spotted (MEDIUM)")
    elif pest_count > 0:
        pest_sev = "low"
        parts.append(f"{types_str} trace detected — {pest_count} insect(s) (LOW)")
    else:
        pest_sev = "none"

    if fungus_coverage_pct >= SEV_FUNGUS_HIGH_PCT:
        fungus_sev = "high"
        parts.append(f"Fungus infestation — {fungus_coverage_pct:.1f}% leaf coverage (HIGH)")
    elif fungus_coverage_pct >= SEV_FUNGUS_MEDIUM_PCT:
        fungus_sev = "medium"
        parts.append(f"Fungus spreading — {fungus_coverage_pct:.1f}% leaf coverage (MEDIUM)")
    elif fungus_coverage_pct > 0.0:
        fungus_sev = "low"
        parts.append(f"Fungus detected — {fungus_coverage_pct:.1f}% leaf coverage (LOW)")
    else:
        fungus_sev = "none"

    desc = " | ".join(parts) if parts else "No threats detected — plant is healthy."
    return pest_sev, fungus_sev, desc


_SEV_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _worse_severity(a, b):
    """Legacy single-value severity for anything that still needs just one
    number (old API consumers). Prefer pest_severity/fungus_severity directly
    wherever you can — this collapses the two back into one and by
    definition can't tell you which of the two is actually the problem."""
    return a if _SEV_ORDER.get(a, 0) >= _SEV_ORDER.get(b, 0) else b



def _norm_name(name):
    n = (name or "").strip().lower()
    if n in ("whitefly", "whiteflies", "white_fly"):
        return "whiteflies"
    if n in ("worm", "worms"):
        return "worm"
    if n in ("fungus", "fungi", "mold", "mould"):
        return "fungus"
    return n


def _history_item(lettuce_id, pest_type, confidence, severity_level, fungus_coverage_pct,
                  detected_at=None, model_used="hailo", row_id=None):
    pest_type = _norm_name(pest_type)
    ts = detected_at or datetime.now().isoformat()
    label = {"whiteflies": "Whiteflies", "worm": "Worm", "fungus": "Fungus"}.get(pest_type, pest_type)
    msg = f"{label} @ lettuce {lettuce_id} ({severity_level}, {float(confidence or 0)*100:.0f}%)"
    return {
        "id": row_id or int(time.time() * 1000),
        "lettuce_id": lettuce_id,
        "station": lettuce_id,
        "station_id": lettuce_id,
        "pest_type": pest_type,
        "type": pest_type,
        "name": pest_type,
        "label": label,
        "confidence": float(confidence or 0),
        "severity": severity_level,
        "severity_level": severity_level,
        "fungus_coverage_pct": float(fungus_coverage_pct or 0),
        "detected_at": ts,
        "timestamp": ts,
        "time": ts,
        "created_at": ts,
        "model_used": model_used,
        "message": msg,
        "description": msg,
    }


def get_history(lettuce_id=None, limit=40):
    items = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if lettuce_id is None:
                q = """SELECT id, lettuce_id, pest_type, confidence, severity_level,
                              fungus_coverage_pct, detected_at, model_used
                       FROM detection_logs ORDER BY id DESC LIMIT ?"""
                rows = conn.execute(q, (limit,)).fetchall()
            else:
                q = """SELECT id, lettuce_id, pest_type, confidence, severity_level,
                              fungus_coverage_pct, detected_at, model_used
                       FROM detection_logs WHERE lettuce_id=? ORDER BY id DESC LIMIT ?"""
                rows = conn.execute(q, (lettuce_id, limit)).fetchall()
            for r in rows:
                items.append(_history_item(
                    r["lettuce_id"], r["pest_type"], r["confidence"], r["severity_level"],
                    r["fungus_coverage_pct"], r["detected_at"], r["model_used"], r["id"]
                ))
    except Exception as e:
        print(f"history db: {e}")
    if not items:
        for ev in list(recent_events):
            if lettuce_id is None or ev.get("lettuce_id") == lettuce_id:
                items.append(_history_item(
                    ev.get("lettuce_id"), ev.get("pest_type"), ev.get("confidence"),
                    ev.get("severity_level"), ev.get("fungus_coverage_pct"),
                    ev.get("detected_at"), ev.get("model_used")
                ))
            if len(items) >= limit:
                break
    return items


def log_detection(lettuce_id, pest_type, confidence, model_used, camera_profile,
                  severity_level="low", fungus_coverage_pct=0.0):
    pest_type = _norm_name(pest_type)
    recent_events.appendleft(_history_item(
        lettuce_id, pest_type, confidence, severity_level, fungus_coverage_pct,
        datetime.now().isoformat(), model_used
    ))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO detection_logs
            (lettuce_id, pest_type, confidence, model_used, camera_profile, detected_at,
             severity_level, fungus_coverage_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (lettuce_id, pest_type, confidence, model_used, camera_profile,
              datetime.now().isoformat(), severity_level, fungus_coverage_pct))
        conn.execute("""
            UPDATE lettuce_status
            SET status = ?, pest_count = pest_count + ?, fungus_count = fungus_count + ?,
                last_check = ?, severity_level = ?, fungus_coverage_pct = ?
            WHERE lettuce_id = ?
        """, (
            pest_type,
            1 if pest_type in ('whitefly', 'whiteflies', 'worm') else 0,
            1 if pest_type == 'fungus' else 0,
            datetime.now().isoformat(),
            severity_level,
            fungus_coverage_pct,
            lettuce_id
        ))
        conn.commit()

# ---------------------------------------------------------------------------
# Model path finder (prefers local models/ first)
# ---------------------------------------------------------------------------
def find_hef(filename: str):
    """Search for HEF file. Prefer local models/ directory first."""
    candidates = [
        LOCAL_MODELS_DIR / filename,                          # highest priority (your screenshot)
        SCRIPT_DIR / filename,
        DATASET_BASE.parent / "models" / filename,
        Path.home() / "models" / filename,
        Path.cwd() / "models" / filename,
        Path.cwd() / filename,
        Path(f"/media/aipest/New Volume/models/{filename}"),
        Path(f"/media/aipest/New Volume3/models/{filename}"),
    ]
    # also scan /media and /mnt
    for root in [Path("/media"), Path("/mnt")]:
        if root.exists():
            try:
                for p1 in root.iterdir():
                    if p1.is_dir():
                        candidates.append(p1 / "models" / filename)
                        candidates.append(p1 / filename)
                        try:
                            for p2 in p1.iterdir():
                                if p2.is_dir():
                                    candidates.append(p2 / "models" / filename)
                                    candidates.append(p2 / filename)
                        except Exception:
                            pass
            except Exception:
                pass

    seen = set()
    for c in candidates:
        try:
            if c.exists():
                abs_c = c.resolve()
                if abs_c not in seen:
                    seen.add(abs_c)
                    return abs_c
        except Exception:
            continue
    return None

# ---------------------------------------------------------------------------
# FIXED Dual-Model Hailo Loading (single shared VDevice)
# ---------------------------------------------------------------------------
def _hef_input_hw(input_info):
    """Return (h, w) from a Hailo input vstream info."""
    shape = tuple(input_info[0].shape)
    dims = [int(x) for x in shape if int(x) > 3]
    if len(dims) >= 2:
        # prefer the two spatial dims
        return int(dims[0]), int(dims[1])
    if len(shape) >= 3:
        # (H,W,C) or (C,H,W) or (N,H,W,C)
        if shape[-1] in (1, 3, 4):
            return int(shape[-3]), int(shape[-2])
        if shape[0] in (1, 3, 4) and len(shape) == 3:
            return int(shape[1]), int(shape[2])
    return 640, 640


def _configure_one_hef(label, path, class_names):
    """Configure one HEF onto the shared VDevice. Returns model dict or None."""
    global hailo_vdevice
    try:
        hef = HEF(str(path))
        cfg_params = ConfigureParams.create_from_hef(
            hef=hef, interface=HailoStreamInterface.PCIe
        )
        network_groups = hailo_vdevice.configure(hef, cfg_params)
        ng = network_groups[0]
        in_info = hef.get_input_vstream_infos()
        out_info = hef.get_output_vstream_infos()
        try:
            in_params = InputVStreamParams.make(ng, format_type=FormatType.UINT8)
            out_params = OutputVStreamParams.make(ng, format_type=FormatType.FLOAT32)
        except Exception:
            in_params = InputVStreamParams.make(ng)
            out_params = OutputVStreamParams.make(ng)
        ih, iw = _hef_input_hw(in_info)
        print(f"✅ {label} loaded: {path.name}")
        print(f"   Classes: {dict(enumerate(class_names))}")
        print(f"   Input : {in_info[0].name} shape={in_info[0].shape} → resize {iw}x{ih}")
        quant = getattr(in_info[0], "quant_info", None)
        if quant is not None:
            print(f"   Input quantization: scale={getattr(quant, 'qp_scale', '?')} "
                  f"zero={getattr(quant, 'qp_zp', '?')} "
                  f"range=[{getattr(quant, 'limvals_min', '?')}, {getattr(quant, 'limvals_max', '?')}]")
        for o in out_info:
            print(f"   Output: {o.name} shape={o.shape}")
        return {
            "label": label,
            "path": str(path),
            "group": ng,
            "input_info": in_info,
            "input_params": in_params,
            "output_params": out_params,
            "class_names": class_names,
            "in_h": ih,
            "in_w": iw,
        }
    except Exception as e:
        print(f"❌ {label} failed ({path}): {e}")
        traceback.print_exc()
        return None


def _hef_specs():
    return [
        ("COMB", "best.hef", CLASS_NAMES),
        ("SEG", "best_seg.hef", SEG_CLASS_NAMES),
        ("DET", "best_detect.hef", DETECT_CLASS_NAMES),
    ]


def _discover_hefs():
    found = []
    for label, fname, classes in _hef_specs():
        path = find_hef(fname)
        print(f"   {label:4} {fname:18} : {path if path else 'NOT FOUND'}")
        if path:
            found.append((label, path, classes))
    return found


def _set_loaded_flags():
    global seg_model_loaded, det_model_loaded, comb_model_loaded, model
    labels = {m["label"] for m in hailo_models}
    seg_model_loaded = "SEG" in labels
    det_model_loaded = "DET" in labels
    comb_model_loaded = "COMB" in labels
    model = True if hailo_models else None


def hailo_release_device():
    global hailo_vdevice, hailo_models
    hailo_models = []
    _set_loaded_flags()
    if hailo_vdevice is None:
        return
    try:
        hailo_vdevice.release()
        print("♻️  Hailo VDevice released")
    except Exception as e:
        print(f"⚠ VDevice release: {e}")
    hailo_vdevice = None


def hailo_open_device():
    global hailo_vdevice
    if hailo_vdevice is not None:
        return True
    try:
        hailo_vdevice = VDevice()
        print("✅ Shared VDevice created")
        return True
    except Exception as e:
        print(f"❌ VDevice failed: {e}")
        traceback.print_exc()
        hailo_vdevice = None
        return False


def hailo_load_labels(labels):
    """Release chip, then configure only the requested labels from catalog."""
    global hailo_models
    want = [item for item in hailo_catalog if item[0] in labels]
    if len(want) != len(set(labels)):
        print(f"⚠ No catalog entries for {labels}")
        return False
    with hailo_swap_lock:
        hailo_release_device()
        if not hailo_open_device():
            return False
        hailo_models = []
        for label, path, classes in want:
            entry = _configure_one_hef(label, path, classes)
            if entry is not None:
                hailo_models.append(entry)
        if len(hailo_models) != len(want):
            print(f"❌ Requested {labels}, but only {[m['label'] for m in hailo_models]} loaded")
            hailo_release_device()
            return False
        _set_loaded_flags()
        print(f"✅ Hailo active now: {[m['label'] for m in hailo_models]}")
        return bool(hailo_models)


def init_hailo_models():
    """Load HEFs according to HAILO_MODE (split / combined / rotate / all)."""
    global hailo_catalog, hailo_models, model

    hailo_models = []
    hailo_catalog = []
    _set_loaded_flags()

    if not HAS_HAILO:
        print("❌ Hailo platform not available")
        return

    print("\n" + "=" * 60)
    print(f"🧠 Loading Hailo models  mode={HAILO_MODE}")
    print("=" * 60)

    hailo_catalog = _discover_hefs()
    if not hailo_catalog:
        print("❌ No HEF models found in models/")
        return

    have = {lab for lab, _, _ in hailo_catalog}
    mode = (HAILO_MODE or "auto").lower()

    if mode == "auto":
        # Two specialized HEFs cannot share the chip with best.hef.
        # Prefer the split pair when both exist; otherwise combined.
        mode = "split" if ("SEG" in have and "DET" in have) else "combined"
        print(f"   auto → {mode}")

    if mode == "combined":
        ok = hailo_load_labels(["COMB"]) or hailo_load_labels(["DET", "SEG"])
    elif mode == "split":
        ok = hailo_load_labels(["DET", "SEG"])
        if not ok:
            print("⚠ split pair failed — falling back to best.hef")
            ok = hailo_load_labels(["COMB"])
    elif mode in ("rotate", "manual"):
        first = "COMB" if "COMB" in have else ("DET" if "DET" in have else "SEG")
        ok = hailo_load_labels([first])
    else:  # "all" — try everything on one device (often only first succeeds)
        if not hailo_open_device():
            return
        hailo_models = []
        for label, path, classes in hailo_catalog:
            entry = _configure_one_hef(label, path, classes)
            if entry is not None:
                hailo_models.append(entry)
        _set_loaded_flags()
        ok = bool(hailo_models)

    if ok:
        print(f"✅ Hailo ready: {[m['label'] for m in hailo_models]}  (mode={mode})")
        if mode == "rotate":
            print("   rotate plan: DETECT_A → DET, DETECT_B → SEG, next plant → COMB")
        if mode == "split":
            print("   best.hef is NOT loaded so best_seg + best_detect can share the chip")
    else:
        print("❌ No models loaded successfully")

def init_pt_model():
    global pt_model
    if not HAS_ULTRALYTICS:
        return
    search_paths = [
        SCRIPT_DIR / "best.pt",
        SCRIPT_DIR / "models" / "best.pt",
        DATASET_BASE.parent / "models" / "best.pt",
        Path.cwd() / "best.pt",
        Path.home() / "models" / "best.pt",
    ]
    for p in search_paths:
        if p.exists():
            try:
                print(f"🧠 Loading PyTorch fallback: {p}")
                pt_model = YOLO(str(p))
                print(f"✅ PyTorch model loaded. Classes: {pt_model.names}")
                return
            except Exception as e:
                print(f"⚠ PyTorch load error: {e}")
    print("⚠ No best.pt found — PyTorch fallback disabled")

# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------
def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))

def _softmax(x, axis=-1):
    e = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return e / np.sum(e, axis=axis, keepdims=True)

def _dfl_decode(box_preds):
    N = box_preds.shape[0]
    box_preds = box_preds.reshape(N, 4, REG_MAX)
    probs = _softmax(box_preds, axis=2)
    proj = np.arange(REG_MAX, dtype=np.float32)
    distances = np.sum(probs * proj.reshape(1, 1, -1), axis=2)
    return distances

class TemporalConsensus:
    """Requires a detection to persist across frames before we trust it.

    Fungus and worms are effectively stationary between frames, so a tight
    IoU match against the same pixel region works well for them. Whiteflies
    are small and move erratically — the same insect can land 20-30px away
    a tenth of a second later, so a strict IoU match almost always fails to
    "reconnect" it across frames and it gets thrown out as noise even when
    it's real. That's why fungus/worm consensus felt fine but whiteflies
    would build up "high" severity on the dashboard (single-frame clusters)
    while almost never surviving into a confirmed, actionable detection.
    We use a looser, class-aware match: a small min-IoU floor PLUS a
    center-distance fallback scaled to the box size, and whiteflies need
    fewer repeat sightings (they're fleeting) rather than tight overlap.
    """

    def __init__(self, buffer_size=5, min_hits=3, iou_threshold=0.3):
        self.buffer = deque(maxlen=buffer_size)
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        # Per-class overrides: (min_hits, iou_threshold, max_center_dist_frac)
        # max_center_dist_frac is the allowed center-to-center drift as a
        # fraction of the box's own diagonal — lets a fast-moving whitefly
        # still "match" itself across frames even with near-zero IoU.
        self.class_overrides = {
            "whiteflies": {"min_hits": 2, "iou_threshold": 0.05, "max_center_dist_frac": 1.5},
            "whitefly": {"min_hits": 2, "iou_threshold": 0.05, "max_center_dist_frac": 1.5},
        }

    def _iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        if inter == 0:
            return 0
        areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        return inter / float(areaA + areaB - inter)

    def _center_dist_ok(self, boxA, boxB, max_frac):
        cxA, cyA = (boxA[0] + boxA[2]) / 2.0, (boxA[1] + boxA[3]) / 2.0
        cxB, cyB = (boxB[0] + boxB[2]) / 2.0, (boxB[1] + boxB[3]) / 2.0
        dist = ((cxA - cxB) ** 2 + (cyA - cyB) ** 2) ** 0.5
        diagA = ((boxA[2] - boxA[0]) ** 2 + (boxA[3] - boxA[1]) ** 2) ** 0.5
        return dist <= max_frac * max(diagA, 1.0)

    def update(self, detections):
        self.buffer.append(detections)
        confirmed = []
        for det in detections:
            ov = self.class_overrides.get(det[4], {})
            min_hits = ov.get("min_hits", self.min_hits)
            iou_thr = ov.get("iou_threshold", self.iou_threshold)
            max_dist_frac = ov.get("max_center_dist_frac")
            if len(self.buffer) < min_hits:
                continue
            hits = 1
            for past in list(self.buffer)[:-1]:
                for pdet in past:
                    if pdet[4] != det[4]:
                        continue
                    matched = self._iou(det[:4], pdet[:4]) > iou_thr
                    if not matched and max_dist_frac is not None:
                        matched = self._center_dist_ok(det[:4], pdet[:4], max_dist_frac)
                    if matched:
                        hits += 1
                        break
            if hits >= min_hits:
                confirmed.append(det)
        return confirmed

# NOTE: min_hits=1 previously meant every single-frame detection auto-passed
# ("hits" starts at 1, which already satisfied min_hits=1) — the consensus
# filter was doing nothing. That's why a single noisy frame (a dark speck,
# a worm-shaped shadow) could flash on the dashboard as a confirmed hit,
# and why the same blob could flicker between "fungus" and "worm" from one
# frame to the next. Requiring 3 of the last 5 frames to agree on both
# class AND location makes a detection "stick" only when it's real.
temporal = TemporalConsensus(buffer_size=5, min_hits=3, iou_threshold=0.3)
_infer_debug = {
    "printed": False,
    "frames": 0,
    "last_shapes": "",
    "last_count": 0,
    "last_err": "",
    "max_score": 0.0,
    "det_shapes": "",
    "seg_shapes": "",
    "det_max": 0.0,
    "seg_max": 0.0,
    "seg_dets_seen": 0,
    "seg_masks_seen": 0,
    "seg_mask_warned": False,
}

def nms(boxes, scores, iou_threshold=0.45):
    if len(boxes) == 0:
        return []
    boxes = np.array(boxes, dtype=np.float32)
    scores = np.array(scores, dtype=np.float32)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        inds = np.where(ovr <= iou_threshold)[0]
        order = order[inds + 1]
    return keep

def _summarize_outputs(outputs):
    parts = []
    for name, tensor in outputs.items():
        try:
            arr = tensor if isinstance(tensor, np.ndarray) else np.array(tensor)
            parts.append(f"{name}{tuple(arr.shape)}")
        except Exception:
            parts.append(f"{name}:{type(tensor)}")
    return ", ".join(parts)


def _record_class_scores(scores, class_names):
    """Record pre-filter probabilities, including classes that win no anchors."""
    if scores.size:
        for i, name in enumerate(class_names):
            peak = float(np.max(scores[:, i]))
            previous = _infer_debug["class_max"].get(name, 0.0)
            _infer_debug["class_max"][name] = max(previous, peak)


def _decode_nms_tensor(tensor, class_names, img_w, img_h, conf_thres):
    """Hailo often compiles YOLO with an NMS postprocess head.

    Typical shapes:
      (1, N, 5+C), (N, 5+C), (1, N, 6), (N, 6)
    Layouts seen in the wild:
      x1,y1,x2,y2,score,class
      x,y,w,h,score + class scores
    """
    arr = tensor if isinstance(tensor, np.ndarray) else np.array(tensor)
    while arr.ndim > 2 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim != 2 or arr.shape[0] == 0:
        return []
    n, d = arr.shape
    if d < 5:
        return []

    results = []
    n_cls = len(class_names)
    for row in arr:
        row = row.astype(np.float32)
        # skip empty padded proposals
        if np.all(np.abs(row) < 1e-8):
            continue

        if d == 6:
            x1, y1, x2, y2, score, cls_id = row[:6]
            # if values look like xywh + score + class
            if x2 < x1 or y2 < y1:
                cx, cy, w, h = row[0], row[1], row[2], row[3]
                x1, y1 = cx - w / 2, cy - h / 2
                x2, y2 = cx + w / 2, cy + h / 2
            cls_id = int(round(float(cls_id)))
        elif d >= 4 + n_cls:
            # xyxy or xywh + class scores
            coords = row[:4]
            scores = row[4:4 + n_cls]
            # some HEFs put objectness at [4] then classes
            if d >= 5 + n_cls:
                obj = _sigmoid(row[4])
                scores = _sigmoid(row[5:5 + n_cls]) * obj
            else:
                scores = _sigmoid(scores)
            cls_id = int(np.argmax(scores))
            score = float(scores[cls_id])
            x1, y1, x2, y2 = coords
            if x2 < x1 or y2 < y1:
                cx, cy, w, h = coords
                x1, y1 = cx - w / 2, cy - h / 2
                x2, y2 = cx + w / 2, cy + h / 2
        else:
            x1, y1, x2, y2, score = row[:5]
            cls_id = int(round(float(row[5]))) if d > 5 else 0
            if x2 < x1 or y2 < y1:
                cx, cy, w, h = row[0], row[1], row[2], row[3]
                x1, y1 = cx - w / 2, cy - h / 2
                x2, y2 = cx + w / 2, cy + h / 2

        # Hailo NMS is often already normalized 0-1
        if max(x1, y1, x2, y2) <= 2.0:
            x1, x2 = x1 * img_w, x2 * img_w
            y1, y2 = y1 * img_h, y2 * img_h

        if cls_id < 0 or cls_id >= n_cls:
            continue
        name = class_names[cls_id]
        thr = PER_CLASS_THRESHOLDS.get(name, conf_thres)
        if score < thr:
            continue
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_w, x2), min(img_h, y2)
        if not _valid_box(x1, y1, x2, y2, name):
            continue
        results.append([int(x1), int(y1), int(x2), int(y2), name, float(score)])
    return results


def _valid_box(x1, y1, x2, y2, name):
    width, height = x2 - x1, y2 - y1
    if not np.isfinite([x1, y1, x2, y2]).all() or width <= 0 or height <= 0:
        return False
    if width * height < PER_CLASS_MIN_BOX_AREA.get(name, MIN_BOX_AREA):
        return False
    return name == "fungus" or MIN_ASPECT_RATIO <= width / height <= MAX_ASPECT_RATIO


def _decode_hailo_nms(tensor, class_names, img_w, img_h, conf_thres):
    """InferVStreams NMS: batch -> class -> Nx5, or TF [1,C,5,N].

    Hailo's five values are ymin,xmin,ymax,xmax,score, not xyxy.
    Preserve empty classes and singleton boxes; never squeeze class axes.
    """
    if isinstance(tensor, np.ndarray):
        arr = tensor
        if arr.ndim == 4 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.ndim != 3 or arr.shape[0] != len(class_names) or arr.shape[1] != 5:
            raise ValueError(f"Unsupported Hailo NMS array shape: {arr.shape}")
        classes = [rows.T for rows in arr]
    else:
        # Default HailoRT Python representation includes the batch dimension.
        if len(tensor) != 1 or len(tensor[0]) != len(class_names):
            raise ValueError("Expected one Hailo NMS batch with one array per class")
        classes = tensor[0]
    results = []
    max_score = 0.0
    for name, rows in zip(class_names, classes):
        rows = np.asarray(rows, dtype=np.float32)
        if rows.size == 0:
            continue
        if rows.ndim != 2 or rows.shape[1] != 5:
            raise ValueError(f"Unsupported Hailo NMS class shape: {rows.shape}")
        for y1, x1, y2, x2, score in rows:
            if not np.isfinite([x1, y1, x2, y2, score]).all():
                continue
            max_score = max(max_score, float(score))
            _infer_debug.setdefault("class_max", {})[name] = max(
                _infer_debug.get("class_max", {}).get(name, 0.0), float(score))
            if score < PER_CLASS_THRESHOLDS.get(name, conf_thres):
                continue
            x1, x2 = np.clip([x1 * img_w, x2 * img_w], 0, img_w)
            y1, y2 = np.clip([y1 * img_h, y2 * img_h], 0, img_h)
            if _valid_box(x1, y1, x2, y2, name):
                results.append([int(x1), int(y1), int(x2), int(y2), name, float(score)])
    _infer_debug["max_score"] = max_score
    return results


def _as_hwc(tensor):
    arr = tensor if isinstance(tensor, np.ndarray) else np.array(tensor)
    # Only remove the batch axis: squeezing HxWx1 drops the fungus class head.
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 3:
        # If CHW (C, H, W) with C small and H==W, convert to HWC
        if arr.shape[0] in (1, 2, 3, 4, 32, 64, 80) and arr.shape[1] == arr.shape[2]:
            arr = np.transpose(arr, (1, 2, 0))
    return arr


def _maybe_sigmoid(scores):
    scores = scores.astype(np.float32)
    if scores.size == 0:
        return scores
    mn, mx = float(np.min(scores)), float(np.max(scores))
    # Already probabilities
    if mn >= 0.0 and mx <= 1.0:
        return scores
    return _sigmoid(scores)


def postprocess_yolov8(outputs, img_w, img_h, class_names, conf_thres=None):
    """YOLOv8 detect/seg post-process for raw Hailo heads (NHWC)."""
    if conf_thres is None:
        conf_thres = CONFIDENCE_THRESHOLD
    _infer_debug["class_max"] = {name: 0.0 for name in class_names}

    nms_hits = []
    has_nms = False
    for name, tensor in outputs.items():
        if "nms" in str(name).lower():
            has_nms = True
            nms_hits.extend(_decode_hailo_nms(tensor, class_names, img_w, img_h, conf_thres))
            continue
        try:
            arr = tensor if isinstance(tensor, np.ndarray) else np.array(tensor)
        except Exception:
            continue
        flat = np.squeeze(arr)
        lname = str(name).lower()
        looks_nms = ("nms" in lname) or (
            flat.ndim == 2 and flat.shape[-1] in (5, 6, 4 + len(class_names), 5 + len(class_names))
        )
        if looks_nms:
            nms_hits.extend(_decode_nms_tensor(arr, class_names, img_w, img_h, conf_thres))
    if nms_hits or has_nms:
        nms_hits.sort(key=lambda d: d[5], reverse=True)
        return nms_hits[:MAX_DETECTIONS_PER_FRAME]

    box_tensors = {}
    cls_tensors = {}
    coef_tensors = {}
    proto = None
    n_cls = len(class_names)

    for name, tensor in outputs.items():
        try:
            arr = _as_hwc(tensor)
        except Exception:
            continue
        if arr.ndim != 3:
            continue
        h, w, c = arr.shape
        if h == 160 and w == 160 and c == 32:
            proto = arr
        elif c == 64 and h in (20, 40, 80):
            box_tensors[h] = arr
        elif c == 32 and h in (20, 40, 80):
            coef_tensors[h] = arr
        elif c == n_cls:
            if h in (20, 40, 80):
                cls_tensors[h] = arr

    global_max = 0.0
    if not box_tensors or not cls_tensors:
        raise ValueError(f"Unsupported YOLO output heads: {_summarize_outputs(outputs)}")

    all_boxes, all_scores, all_classes, all_coefs = [], [], [], []
    strides = {80: 8, 40: 16, 20: 32}

    for scale, stride in strides.items():
        if scale not in box_tensors or scale not in cls_tensors:
            continue
        box_pred = box_tensors[scale]
        cls_pred = cls_tensors[scale]
        H, W = box_pred.shape[:2]
        box_pred = box_pred.reshape(-1, 64)
        cls_pred = cls_pred.reshape(-1, cls_pred.shape[-1])
        coef_pred = None
        if scale in coef_tensors:
            coef_pred = coef_tensors[scale].reshape(-1, 32)

        scores = _maybe_sigmoid(cls_pred)
        _record_class_scores(scores, class_names)
        class_ids = np.argmax(scores, axis=1)
        max_scores = scores[np.arange(len(scores)), class_ids]
        if max_scores.size:
            global_max = max(global_max, float(np.max(max_scores)))

        thresholds = np.array([PER_CLASS_THRESHOLDS.get(class_names[int(c)], conf_thres)
                               for c in class_ids])
        mask = max_scores >= thresholds
        if not np.any(mask):
            continue

        box_pred = box_pred[mask]
        max_scores = max_scores[mask]
        class_ids = class_ids[mask]
        if coef_pred is not None:
            coef_pred = coef_pred[mask]

        distances = _dfl_decode(box_pred)
        gy, gx = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
        anchors = np.stack([gx.ravel(), gy.ravel()], axis=1)[mask]

        x1 = (anchors[:, 0] + 0.5 - distances[:, 0]) * stride
        y1 = (anchors[:, 1] + 0.5 - distances[:, 1]) * stride
        x2 = (anchors[:, 0] + 0.5 + distances[:, 2]) * stride
        y2 = (anchors[:, 1] + 0.5 + distances[:, 3]) * stride
        boxes = np.stack([x1, y1, x2, y2], axis=1)
        boxes[:, 0] = np.clip(boxes[:, 0], 0, img_w)
        boxes[:, 1] = np.clip(boxes[:, 1], 0, img_h)
        boxes[:, 2] = np.clip(boxes[:, 2], 0, img_w)
        boxes[:, 3] = np.clip(boxes[:, 3], 0, img_h)

        all_boxes.append(boxes)
        all_scores.append(max_scores)
        all_classes.append(class_ids)
        if coef_pred is not None:
            all_coefs.append(coef_pred)

    _infer_debug["max_score"] = global_max
    if not all_boxes:
        return []

    all_boxes = np.concatenate(all_boxes, axis=0)
    all_scores = np.concatenate(all_scores, axis=0)
    all_classes = np.concatenate(all_classes, axis=0)
    all_coefs = np.concatenate(all_coefs, axis=0) if all_coefs else None

    # A fungus patch must not suppress an overlapping insect of another class.
    keep = []
    for cls_id in np.unique(all_classes):
        indices = np.flatnonzero(all_classes == cls_id)
        keep.extend(indices[nms(all_boxes[indices], all_scores[indices], NMS_IOU_THRESHOLD)])
    results = []
    for i in keep:
        cls_id = int(all_classes[i])
        if cls_id >= len(class_names):
            continue
        name = class_names[cls_id]
        thr = PER_CLASS_THRESHOLDS.get(name, conf_thres)
        if all_scores[i] < thr:
            continue
        x1, y1, x2, y2 = all_boxes[i]
        if not _valid_box(x1, y1, x2, y2, name):
            continue
        # Aspect-ratio filtering exists to reject thin sliver-shaped noise
        # being mistaken for an insect body — it doesn't make sense for
        # fungus, which is an irregular, organically-shaped disease region
        # that can legitimately run long and thin along a leaf edge or vein.
        if name != "fungus":
            aspect = (x2 - x1) / max(1.0, (y2 - y1))
            if aspect < MIN_ASPECT_RATIO or aspect > MAX_ASPECT_RATIO:
                continue
        poly = None
        mask_pixels = 0
        if proto is not None and all_coefs is not None:
            poly, mask_pixels = _mask_to_polygon_and_area(proto, all_coefs[i], x1, y1, x2, y2, img_w, img_h)
        results.append([int(x1), int(y1), int(x2), int(y2), name, float(all_scores[i]), poly, mask_pixels])

    results.sort(key=lambda r: r[5], reverse=True)
    return results[:MAX_DETECTIONS_PER_FRAME]


def _mask_to_polygon_and_area(proto, coeffs, x1, y1, x2, y2, img_w, img_h):
    """Return (polygon_points, mask_pixel_count_in_full_image_coords)."""
    try:
        ph, pw, pc = proto.shape
        proto_flat = proto.reshape(-1, pc)          # (160*160, 32)
        mask = proto_flat @ coeffs.reshape(pc, 1)   # (160*160, 1)
        # Prototype/coefficients produce logits, including logits entirely in [0,1].
        mask = _sigmoid(mask).reshape(ph, pw)
        # scale box into proto space
        sx, sy = pw / float(img_w), ph / float(img_h)
        x1p, x2p = int(max(0, x1 * sx)), int(min(pw, x2 * sx))
        y1p, y2p = int(max(0, y1 * sy)), int(min(ph, y2 * sy))
        if x2p - x1p < 2 or y2p - y1p < 2:
            return None, 0
        crop = mask[y1p:y2p, x1p:x2p]
        binm = (crop > 0.5).astype(np.uint8) * 255
        contours, _ = cv2.findContours(binm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None, 0
        # BUG FIX: this used to gate on max(contours, key=cv2.contourArea) —
        # the SINGLE LARGEST blob only. Real fungal disease very often shows
        # as many small scattered lesions across a leaf rather than one
        # solid patch, so a box that's genuinely full of fungus could have
        # no single blob big/solid enough to pass on its own, and the whole
        # detection got thrown out as "noise" even with heavy infection
        # visibly present. Gate on the TOTAL confident-pixel count instead
        # (already summed below), which reflects all the scattered spots
        # together, not just whichever one happens to be biggest.
        mask_px_proto = int(np.sum(binm > 0))
        crop_area = max(1, (x2p - x1p) * (y2p - y1p))
        if mask_px_proto < MIN_MASK_AREA_PX:
            return None, 0
        if (mask_px_proto / crop_area) < MIN_MASK_FILL_RATIO:
            return None, 0
        # Scale factor: each proto pixel covers (img_w/pw * img_h/ph) image pixels
        scale_factor = (img_w / pw) * (img_h / ph)
        mask_px_image = int(mask_px_proto * scale_factor)
        # Outline that covers EVERY detected spot in this box (a convex hull
        # over all contours combined), not just the largest one, so the
        # segmentation overlay reflects the whole diseased area on screen.
        all_pts = np.vstack([c.reshape(-1, 2) for c in contours])
        hull = cv2.convexHull(all_pts).reshape(-1, 2).astype(np.float64)
        hull[:, 0] = (hull[:, 0] + x1p) * (img_w / pw)
        hull[:, 1] = (hull[:, 1] + y1p) * (img_h / ph)
        return hull.astype(np.int32).tolist(), mask_px_image
    except Exception:
        return None, 0

# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------
def run_inference_on_network(frame_bgr, entry):
    """Run inference on one loaded HEF. Returns list of detections."""
    network_group = entry["group"]
    input_info = entry["input_info"]
    input_params = entry["input_params"]
    output_params = entry["output_params"]
    class_names = entry["class_names"]
    ih, iw = entry["in_h"], entry["in_w"]
    label = entry["label"]

    if network_group is None:
        return []

    try:
        img = cv2.resize(frame_bgr, (iw, ih))
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        input_data = np.expand_dims(img_rgb.astype(np.uint8), axis=0)

        input_name = input_info[0].name

        with network_group.activate():
            with InferVStreams(network_group, input_params, output_params) as pipeline:
                results = pipeline.infer({input_name: input_data})

                if isinstance(results, list):
                    results = {f"out_{i}": t for i, t in enumerate(results)}
                elif not isinstance(results, dict):
                    print(f"⚠ Unexpected results type: {type(results)}")
                    return []

                shapes = _summarize_outputs(results)
                _infer_debug["last_shapes"] = shapes
                _infer_debug["frames"] += 1
                key = label.lower()
                _infer_debug[f"{key}_shapes"] = shapes
                if _infer_debug["frames"] <= 3:
                    print(f"🧠 Hailo {label} raw outputs:", shapes)

                dets = postprocess_yolov8(results, iw, ih, class_names)
                _infer_debug["last_count"] = len(dets)
                _infer_debug[f"{key}_max"] = _infer_debug.get("max_score", 0.0)
                _infer_debug.setdefault("class_scores", {})[label] = dict(_infer_debug["class_max"])
                _infer_debug.setdefault("accepted_boxes", {})[label] = {
                    name: sum(d[4] == name for d in dets) for name in class_names
                }

                # Diagnostic: is the SEG HEF actually giving us mask data at
                # all? If it's only ever handing back boxes with poly=None,
                # the model was almost certainly compiled/exported with
                # Hailo's built-in NMS postprocessing baked in, which throws
                # away the proto/coefficient tensors this code needs to draw
                # a real segmentation mask — no amount of code-side tuning
                # can recover masks that were never exported. That model
                # would need to be re-exported/re-compiled to expose the raw
                # detection + segmentation heads instead of the NMS output.
                if key == "seg":
                    _infer_debug["seg_dets_seen"] += len(dets)
                    _infer_debug["seg_masks_seen"] += sum(1 for d in dets if len(d) > 6 and d[6])
                    if (not _infer_debug["seg_mask_warned"]
                            and _infer_debug["seg_dets_seen"] >= 20
                            and _infer_debug["seg_masks_seen"] == 0):
                        _infer_debug["seg_mask_warned"] = True
                        print("⚠ SEG produced 20+ boxes without usable masks. "
                              "Check logged output shapes for a prototype tensor and mask coefficients. "
                              "Missing tensors require a corrected HEF export; weak masks may also fail "
                              "the mask filter. Fungus boxes remain available.")

                # Diagnostic: sanity-check the DET model's class balance.
                # DETECT_CLASS_NAMES = ["whiteflies", "worm"] assumes the
                # compiled HEF's class index 0 = whiteflies, 1 = worm. If the
                # HEF was actually exported with the opposite order, every
                # whitefly would silently be logged/scored as "worm" (and
                # vice versa) — pest_count math still works either way, but
                # the dashboard label and the spray reason would be wrong,
                # and it'd look like "whiteflies never trigger anything".
                if key == "det":
                    for d in dets:
                        nm = d[4]
                        _infer_debug.setdefault("det_class_counts", {})
                        _infer_debug["det_class_counts"][nm] = _infer_debug["det_class_counts"].get(nm, 0) + 1
                    cc = _infer_debug.get("det_class_counts", {})
                    total_det = sum(cc.values())
                    if total_det >= 100 and not _infer_debug.get("det_balance_warned"):
                        _infer_debug["det_balance_warned"] = True
                        wf_n = cc.get("whiteflies", 0)
                        worm_n = cc.get("worm", 0)
                        if wf_n == 0 or worm_n == 0:
                            print(f"⚠️  DET model class balance looks off after {total_det} detections: "
                                  f"{cc}. If you know both pests are actually present on camera, this can "
                                  f"mean best_detect.hef's class order doesn't match "
                                  f"DETECT_CLASS_NAMES={DETECT_CLASS_NAMES} — check names.json/labels used "
                                  f"at export time against that list.")
                # rescale boxes from model size to 640-space so detect_frame scale stays simple
                if iw != 640 or ih != 640:
                    sx, sy = 640.0 / iw, 640.0 / ih
                    out = []
                    for d in dets:
                        x1, y1, x2, y2, name, conf = d[:6]
                        poly = d[6] if len(d) > 6 else None
                        if poly:
                            poly = [[int(px * sx), int(py * sy)] for px, py in poly]
                        mask_px = int((d[7] if len(d) > 7 else 0) * sx * sy)
                        out.append([int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), name, conf, poly, mask_px])
                    dets = out
                return dets
    except Exception as e:
        _infer_debug["last_err"] = f"{label}: {e}"
        print(f"⚠ Inference error [{label}]: {e}")
        traceback.print_exc()
        # A failed inference is not a healthy frame with zero pests.
        raise RuntimeError(_infer_debug["last_err"]) from e

def _inference_regions(width, height):
    """Full frame plus bounded overlapping square crops in original coordinates."""
    regions = [(0, 0, width, height)]
    if not SMALL_OBJECT_TILES or min(width, height) <= 640:
        return regions
    size = min(min(width, height), max(640, int(np.ceil(max(width, height) / 2)) + 64))
    # No extra passes if cropping would not increase the object's model size.
    if size >= max(width, height):
        return regions
    for y in (0, height - size):
        for x in (0, width - size):
            region = (x, y, x + size, y + size)
            if region not in regions:
                regions.append(region)
    return regions


def _map_crop_detection(det, region, width, height):
    """Map a crop's 640-space result into full-frame 640-space for tracking."""
    left, top, right, bottom = region
    sx, sy = (right - left) / width, (bottom - top) / height
    ox, oy = left * 640.0 / width, top * 640.0 / height
    x1, y1, x2, y2, name, score = det[:6]
    poly = det[6] if len(det) > 6 else None
    if poly:
        poly = [[int(px * sx + ox), int(py * sy + oy)] for px, py in poly]
    pixels = int((det[7] if len(det) > 7 else 0) * sx * sy)
    return [x1 * sx + ox, y1 * sy + oy, x2 * sx + ox, y2 * sy + oy,
            name, score, poly, pixels]


def _merge_region_detections(detections):
    """Deduplicate overlapping crops by class before counting or confirmation."""
    result = []
    for name in sorted({d[4] for d in detections}):
        group = [d for d in detections if d[4] == name]
        keep = nms([d[:4] for d in group], [d[5] for d in group], NMS_IOU_THRESHOLD)
        result.extend(group[i] for i in keep)
    return sorted(result, key=lambda d: d[5], reverse=True)[:MAX_DETECTIONS_PER_FRAME]


def detect_frame(frame_bgr):
    """Run every loaded HEF and merge results."""
    global latest_detections

    all_dets = []
    height, width = frame_bgr.shape[:2]
    regions = _inference_regions(width, height)
    class_scores, accepted_boxes = {}, {}
    with hailo_swap_lock:
        snapshot = list(hailo_models)
        # Keep the device alive until all inference contexts have closed.
        for entry in snapshot:
            label = entry["label"]
            class_scores[label] = {name: 0.0 for name in entry["class_names"]}
            accepted_boxes[label] = {name: 0 for name in entry["class_names"]}
            for index, region in enumerate(regions):
                left, top, right, bottom = region
                dets = run_inference_on_network(frame_bgr[top:bottom, left:right], entry)
                for name, score in _infer_debug.get("class_scores", {}).get(label, {}).items():
                    class_scores[label][name] = max(class_scores[label][name], score)
                for det in dets:
                    # Preserve the existing full-frame worm path. Extra crops
                    # target the small fungus/whitefly issue specifically.
                    if index and det[4] not in ("fungus", "whiteflies", "whitefly"):
                        continue
                    accepted_boxes[label][det[4]] += 1
                    all_dets.append(_map_crop_detection(det, region, width, height))
    all_dets = _merge_region_detections(all_dets)
    _infer_debug["class_scores"] = class_scores
    _infer_debug["accepted_boxes"] = accepted_boxes
    _infer_debug["regions_per_model"] = len(regions)
    _infer_debug["last_err"] = ""

    # Temporal filter  (strip mask_pixels for the tracker, re-attach after)
    stripped = [[d[0], d[1], d[2], d[3], d[4], d[5], d[6] if len(d) > 6 else None] for d in all_dets]
    mask_pxs = [d[7] if len(d) > 7 else 0 for d in all_dets]
    stripped = temporal.update(stripped)
    _infer_debug["confirmed_boxes"] = {
        name: sum(d[4] == name for d in stripped) for name in CLASS_NAMES
    }
    # Re-match mask_pxs by index (temporal may filter)
    # Simple approach: just carry 0 for filtered ones
    all_dets_full = []
    for sd in stripped:
        mpx = 0
        for orig, orig_mpx in zip([[d[0], d[1], d[2], d[3], d[4], d[5], d[6] if len(d) > 6 else None] for d in all_dets], mask_pxs):
            if sd[0] == orig[0] and sd[1] == orig[1] and sd[4] == orig[4]:
                mpx = orig_mpx
                break
        all_dets_full.append(sd + [mpx])

    # Boxes come from 640x640 inference; scale them onto the live frame
    h, w = frame_bgr.shape[:2]
    sx = w / 640.0
    sy = h / 640.0
    scaled = []
    for det in all_dets_full:
        x1, y1, x2, y2, name, conf = det[:6]
        poly = det[6] if len(det) > 6 else None
        mask_px = det[7] if len(det) > 7 else 0
        if poly:
            poly = [[int(px * sx), int(py * sy)] for px, py in poly]
        # Scale mask pixel count too
        mask_px = int(mask_px * sx * sy)
        scaled.append([
            int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy), name, conf, poly, mask_px
        ])
    all_dets = scaled

    # Prepare for drawing
    colors = {
        "fungus": (0, 165, 255),
        "whiteflies": (0, 255, 255),
        "whitefly": (0, 255, 255),
        "worm": (0, 0, 255),
    }
    draw_list = []
    for det in all_dets:
        x1, y1, x2, y2, name, conf = det[:6]
        poly = det[6] if len(det) > 6 else None
        color = colors.get(name, (0, 255, 0))
        label = f"{name} {conf:.2f}"
        draw_list.append((x1, y1, x2, y2, label, color, poly))

    latest_detections = draw_list
    return all_dets

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
def init_camera():
    global camera
    if not HAS_CAMERA:
        raise RuntimeError("picamera2 is unavailable; camera detection cannot start")
    try:
        camera = Picamera2()
        config = camera.create_video_configuration(
            main={"size": (4056, 3040), "format": "RGB888"},
            lores={"size": (1280, 960), "format": "RGB888"},
            buffer_count=4,
        )
        camera.configure(config)
        camera.start()
        time.sleep(0.5)
        camera.set_controls({
            "AeEnable": True,
            "AwbEnable": True,
            "Sharpness": 1.0,
            "Contrast": 1.0,
            "Saturation": 1.0,
        })
        print("📷 Camera started (12.3MP + 960p)")
    except Exception as e:
        if camera is not None:
            camera.close()
        camera = None
        raise RuntimeError(
            f"Camera could not start: {e}. Check 'sudo systemctl status farmcam' "
            "and close the other camera process before starting one FarmCam instance."
        ) from e

def capture_frames():
    global current_frame, frame_sequence, frame_timestamp
    while not stop_event.is_set():
        if camera and HAS_CAMERA:
            try:
                # Picamera2 RGB888 arrays already have OpenCV's B,G,R byte order.
                frame_bgr = camera.capture_array("lores")
                with frame_lock:
                    current_frame = frame_bgr
                    frame_sequence += 1
                    frame_timestamp = time.monotonic()
            except Exception as e:
                _infer_debug["last_err"] = f"Camera capture: {e}"
                with frame_lock:
                    current_frame = None
                print(f"📷 Capture failed: {e}")
                stop_event.wait(1.0)
        stop_event.wait(0.033)

def generate_mjpeg():
    while True:
        with frame_lock:
            frame = current_frame.copy() if current_frame is not None else None
        if frame is not None:
            # HARDENING: previously nothing here was guarded. One malformed
            # detection (a degenerate polygon, an out-of-range box) throwing
            # inside cv2.fillPoly/putText/imencode used to kill this entire
            # generator — which is exactly what makes an MJPEG stream look
            # like it randomly "cuts out and reconnects": the client's
            # onDone/onError fires, it waits 3s, and reconnects from
            # scratch. Now a bad frame just gets skipped instead of taking
            # the whole stream down.
            try:
                for item in list(latest_detections):
                    try:
                        x1, y1, x2, y2, label, color = item[:6]
                        poly = item[6] if len(item) > 6 else None
                        pad = 4
                        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
                        x2, y2 = min(frame.shape[1], x2 + pad), min(frame.shape[0], y2 + pad)
                        if x2 <= x1 or y2 <= y1:
                            continue
                        if poly:
                            # Segmentation result: draw the actual mask outline/fill
                            # and STOP there. Previously a full rectangle + thick
                            # corner brackets were drawn on top of every detection
                            # regardless of whether a mask existed, which visually
                            # buried the segmentation shape under a bounding box —
                            # that's why "segmentation" always looked like a plain
                            # box. Only fall back to a bounding box when there's no
                            # mask to show (pure detector classes, or a seg frame
                            # where the mask decode legitimately found nothing).
                            pts = np.array(poly, dtype=np.int32).reshape(-1, 1, 2)
                            if len(pts) < 3:
                                raise ValueError("degenerate polygon")
                            overlay = frame.copy()
                            cv2.fillPoly(overlay, [pts], color)
                            cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, frame)
                            cv2.polylines(frame, [pts], True, color, 2)
                        else:
                            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
                            overlay = frame.copy()
                            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
                            cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
                            l = max(10, min(24, int(min(x2 - x1, y2 - y1) * 0.22)))
                            for pts in [((x1,y1),(x1+l,y1),(x1,y1+l)),
                                        ((x2,y1),(x2-l,y1),(x2,y1+l)),
                                        ((x1,y2),(x1+l,y2),(x1,y2-l)),
                                        ((x2,y2),(x2-l,y2),(x2,y2-l))]:
                                cv2.line(frame, pts[0], pts[1], color, 4)
                                cv2.line(frame, pts[0], pts[2], color, 4)
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        parts = label.split()
                        name_part = parts[0].upper() if parts else "OBJ"
                        conf_val = ""
                        if len(parts) > 1:
                            try:
                                c = float(parts[1])
                                conf_val = f": {int(c*100)}%" if c <= 1 else f": {int(c)}%"
                            except Exception:
                                conf_val = f": {parts[1]}"
                        clean = f"{name_part}{conf_val}"
                        (tw, th), _ = cv2.getTextSize(clean, font, 0.40, 1)
                        text_y = y2 + th + 10 if y2 + th + 10 < frame.shape[0] - 10 else y1 - 10
                        cv2.putText(frame, clean, (x1, text_y), font, 0.40, (0,0,0), 3, cv2.LINE_AA)
                        cv2.putText(frame, clean, (x1, text_y), font, 0.40, color, 1, cv2.LINE_AA)
                    except Exception as e:
                        print(f"⚠️  Skipped one bad detection while drawing: {e}")
                        continue
                hud = (
                    f"NPU C:{int(comb_model_loaded)} S:{int(seg_model_loaded)} D:{int(det_model_loaded)} "
                    f"objs:{len(latest_detections)} max:{_infer_debug.get('max_score', 0):.2f} "
                    f"{current_state} S{active_station_id}"
                )
                cv2.rectangle(frame, (8, 8), (8 + 12 * len(hud), 36), (0, 0, 0), -1)
                cv2.putText(frame, hud, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 180), 1, cv2.LINE_AA)
                _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                print(f"⚠️  Skipped one bad video frame instead of dropping the stream: {e}")
        else:
            try:
                blank = np.zeros((960, 1280, 3), dtype=np.uint8)
                cv2.putText(blank, "FARMCAM - WAITING FOR CAMERA", (300, 480),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,255,255), 2)
                _, jpeg = cv2.imencode('.jpg', blank)
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                print(f"⚠️  Skipped blank placeholder frame: {e}")
        time.sleep(0.066)

def generate_raw_mjpeg():
    while True:
        with frame_lock:
            frame = current_frame.copy() if current_frame is not None else None
        if frame is not None:
            try:
                _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                print(f"⚠️  Skipped one bad raw frame: {e}")
        time.sleep(0.066)

# ---------------------------------------------------------------------------
# Detection loop
# ---------------------------------------------------------------------------
def _reset_window_best():
    global window_best, _dwell_hits_peak, _dwell_sightings
    window_best = {"pest_severity": "none", "fungus_severity": "none", "severity": "none",
                   "desc": "No threats detected — plant is healthy.",
                   "pest": 0, "fungus": 0.0, "types": [], "confidence": 0.0}
    _dwell_hits_peak = 0
    _dwell_sightings = 0
    temporal.buffer.clear()


def _publish_station(sid, summary, extra=None):
    st = stations_data.get(sid)
    if st is None:
        return
    pest = int(summary.get("pest", 0) or 0)
    fungus = float(summary.get("fungus", 0) or 0)
    types = list(summary.get("types", []) or [])
    # Two INDEPENDENT tracks — see compute_severity_info / _note_window.
    # "severity" is kept only as a legacy combined fallback for old API
    # consumers; don't use it to decide what's actually wrong with the plant.
    pest_sev = summary.get("pest_severity", "none") or "none"
    fungus_sev = summary.get("fungus_severity", "none") or "none"
    sev = summary.get("severity") or _worse_severity(pest_sev, fungus_sev)
    desc = summary.get("desc", "") or ""
    if pest > 0 and fungus > 0:
        status = "both"
    elif pest > 0:
        status = "pest"
    elif fungus > 0:
        status = "fungus"
    else:
        status = "clear"
    st["status"] = status
    st["pest_count"] = pest
    st["fungus_count"] = 1 if fungus > 0 else 0
    st["pest_severity_level"] = pest_sev
    st["fungus_severity_level"] = fungus_sev
    st["severity_level"] = sev
    st["fungus_coverage_pct"] = round(fungus, 1)
    st["pest_types"] = types
    st["infestation_desc"] = desc
    st["last_check"] = datetime.now().isoformat()
    if "confidence" in summary:
        st["confidence"] = float(summary["confidence"])
    if extra:
        st.update(extra)


def _note_window(pest_severity, fungus_severity, desc, pest_count, fungus_cov, pest_types, conf=0.0):
    """Track the worst pest reading AND the worst fungus reading seen so far
    in this 30+30s dwell — as two SEPARATE peaks.

    BUG FIX: this used to keep a single snapshot picked by comparing one
    combined severity ordinal. That meant whichever problem was "worse" in
    a given frame would silently overwrite the OTHER one's count too — e.g.
    a frame with 3 whiteflies (pest=HIGH) could stomp out an earlier frame's
    18% fungus coverage (fungus=HIGH) if the pest read as equal-or-worse
    that frame, because the whole dict got replaced together. Now each
    track only updates when ITS OWN severity is at least as bad as what's
    already on record for it — a bad pest frame can never erase a fungus
    reading and vice versa.
    """
    global window_best
    updated = False
    if pest_count > 0 and _SEV_ORDER.get(pest_severity, 0) >= _SEV_ORDER.get(window_best.get("pest_severity", "none"), 0):
        window_best["pest"] = pest_count
        window_best["types"] = list(pest_types)
        updated = True
    if fungus_cov > 0 and _SEV_ORDER.get(fungus_severity, 0) >= _SEV_ORDER.get(window_best.get("fungus_severity", "none"), 0):
        window_best["fungus"] = fungus_cov
        updated = True
    window_best["confidence"] = max(conf, float(window_best.get("confidence", 0.0)))
    if updated:
        # Recompute both severities + a combined description from whatever
        # is now on record for each independent peak (never from this one
        # frame alone), so the two tracks stay internally consistent.
        p_sev, f_sev, combined_desc = compute_severity_info(
            window_best["pest"], window_best["fungus"], window_best["types"]
        )
        window_best["pest_severity"] = p_sev
        window_best["fungus_severity"] = f_sev
        window_best["desc"] = combined_desc
        window_best["severity"] = _worse_severity(p_sev, f_sev)  # legacy combined field


def _advance_station():
    global cycle_index, active_station_id, current_state, state_start_time
    leaving = active_station_id
    cycle_index = (cycle_index + 1) % len(lettuce_sequence)
    active_station_id = lettuce_sequence[cycle_index]
    travel = LOOP_AFTER_4_SECS if leaving == 4 else MOVE_SECS
    current_state = "MOVING"
    state_start_time = time.time()
    _live_alerted.pop(leaving, None)
    _sprayed_this_visit.pop(leaving, None)
    _reset_window_best()
    print(f"🔄 Leaving lettuce {leaving} → lettuce {active_station_id} "
          f"({travel:.0f}s travel, no detection)")
    return travel


def detect_loop():
    global active_station_id, cycle_index, current_state, state_start_time
    global _dwell_hits_peak, _dwell_sightings
    print("🔍 Detection loop started")
    print("   Cycle: 30s detect → 5s pause → 30s detect → 10s move "
          "(15s after lettuce 4)")
    last_log = {}
    last_frame_sequence = -1
    last_diagnostic = 0.0
    move_secs = MOVE_SECS
    _reset_window_best()
    while not stop_event.is_set():
        try:
            elapsed = time.time() - state_start_time

            if current_state == "DETECT_A" and elapsed >= DETECT_WINDOW_SECS:
                print(f"⏸  Lettuce {active_station_id}: 5s pause (no new inference)")
                current_state = "PAUSE_5"
                state_start_time = time.time()
            elif current_state == "PAUSE_5" and elapsed >= PAUSE_SECS:
                if HAILO_MODE.lower() == "rotate":
                    print("♻️  Switching HEF → SEG (fungus) for pass 2")
                    hailo_load_labels(["SEG"]) or hailo_load_labels(["COMB"])
                print(f"🔍 Lettuce {active_station_id}: second 30s detect pass")
                current_state = "DETECT_B"
                state_start_time = time.time()
            elif current_state == "DETECT_B" and elapsed >= DETECT_WINDOW_SECS:
                snap = dict(window_best)
                sid = active_station_id
                # BUG FIX: window_best never carried a "hits" key, so this
                # end-of-visit call always saw hits=0 and silently skipped
                # spraying no matter how severe the visit looked on the
                # dashboard. Feed it the real peak/cumulative counts we
                # tracked across the whole 30+30s dwell.
                snap["hits"] = _dwell_hits_peak
                snap["dwell_sightings"] = _dwell_sightings
                # write final window result to dashboard BEFORE moving
                _publish_station(sid, snap)
                threading.Thread(
                    target=apply_severity_actions, args=(sid, snap), daemon=True
                ).start()
                move_secs = _advance_station()
            elif current_state == "MOVING" and elapsed >= move_secs:
                if HAILO_MODE.lower() == "rotate":
                    nxt = "COMB" if active_station_id % 2 == 0 else "DET"
                    print(f"♻️  Switching HEF → {nxt} for lettuce {active_station_id}")
                    try:
                        hailo_load_labels([nxt]) or hailo_load_labels(["DET"]) or hailo_load_labels(["COMB"])
                    except Exception as e:
                        print(f"⚠ HEF switch skipped: {e}")
                current_state = "DETECT_A"
                state_start_time = time.time()
                _reset_window_best()
                print(f"🔍 Detecting at lettuce {active_station_id} (30s pass 1)")
            elif current_state == "DETECTING":
                current_state = "DETECT_A"

            infer_now = current_state in ("DETECT_A", "DETECT_B", "DETECTING")

            with frame_lock:
                frame = current_frame.copy() if current_frame is not None else None
                sequence = frame_sequence
                frame_age = time.monotonic() - frame_timestamp
            if frame is None or sequence == last_frame_sequence or frame_age > 2.0:
                time.sleep(0.1)
                continue

            if not infer_now:
                time.sleep(DETECTION_INTERVAL)
                continue

            last_frame_sequence = sequence

            # ── coordinator tick (state machine + frame isolation) ──
            # This runs BEFORE detect_frame (frozen) so the machine drives
            # the gating logic; the result of handle_frame replaces raw dets.
            coordinator.tick()
            if _scan_sm := getattr(coordinator, '_scan_sm', None):
                if not _scan_sm.is_detecting:
                    time.sleep(DETECTION_INTERVAL)
                    continue

            dets = detect_frame(frame)

            # ── post-inference: verifier, persistence, buzzer, snapshots ──
            dets = coordinator.handle_frame(frame, dets, sequence)

            pest_count = 0
            fungus_cov = 0.0
            pest_types = set()
            wf = 0
            worms = 0
            frame_dets = []
            total_image_px = max(1, frame.shape[0] * frame.shape[1])
            for det in dets:
                x1, y1, x2, y2, name, conf = det[:6]
                poly = det[6] if len(det) > 6 else None
                mask_px = det[7] if len(det) > 7 else 0
                name = _norm_name(name)
                frame_dets.append({"name": name, "conf": float(conf)})
                if name == "whiteflies":
                    pest_count += 1
                    wf += 1
                    pest_types.add("Whiteflies")
                elif name == "worm":
                    pest_count += 1
                    worms += 1
                    pest_types.add("Worm")
                elif name == "fungus":
                    pest_types.add("Fungus")
                    # Use segmentation mask area if available, otherwise fall back to bbox
                    if mask_px > 0:
                        fungus_cov += (mask_px / total_image_px) * 100.0
                    else:
                        area = (x2 - x1) * (y2 - y1)
                        fungus_cov += (area / total_image_px) * 100.0
            fungus_boxes = sum(1 for d in frame_dets if d["name"] == "fungus")
            total_hits = pest_count + fungus_boxes

            # Track this frame's contribution to the whole 30+30s dwell so
            # the end-of-visit spray check (above) has real numbers instead
            # of always seeing 0. Peak = best single-frame co-occurrence
            # seen anywhere in the dwell; sightings = cumulative evidence,
            # which matters most for whiteflies (rarely 2+ in one frame,
            # but many single sightings over the dwell = a real infestation).
            _dwell_hits_peak = max(_dwell_hits_peak, total_hits)
            _dwell_sightings += total_hits

            frame_conf = max([d["conf"] for d in frame_dets], default=0.0)
            pest_severity, fungus_severity, desc = compute_severity_info(pest_count, fungus_cov, list(pest_types))
            _note_window(pest_severity, fungus_severity, desc, pest_count, fungus_cov, pest_types, conf=frame_conf)

            # Sticky dashboard: an empty frame must not wipe a hit in this window
            pub = window_best if (window_best.get("pest_severity", "none") != "none"
                                   or window_best.get("fungus_severity", "none") != "none") else {
                "pest_severity": pest_severity, "fungus_severity": fungus_severity,
                "severity": _worse_severity(pest_severity, fungus_severity), "desc": desc,
                "pest": pest_count, "fungus": fungus_cov, "types": list(pest_types), "confidence": frame_conf,
            }
            _publish_station(active_station_id, pub, extra={
                "whitefly_count": wf,
                "worm_count": worms,
                "detections": frame_dets[:12],
                "confidence": max([d["conf"] for d in frame_dets], default=0.0),
            })

            # Spray immediately once 2 pest/fungus boxes are in this frame,
            # OR the moment EITHER track independently reads "high" (e.g. a
            # whitefly cluster, or a fungus patch crossing SEV_FUNGUS_HIGH_PCT)
            # even if it didn't literally land 2 boxes in this exact frame.
            now = time.time()
            last_fire = float(_live_alerted.get(f"t{active_station_id}", 0) or 0)
            if (total_hits >= SPRAY_MIN_HITS or pest_severity == "high" or fungus_severity == "high") \
                    and (now - last_fire) >= PUMP_COOLDOWN:
                _live_alerted[f"t{active_station_id}"] = now
                snap = dict(window_best)
                snap["hits"] = max(total_hits, _dwell_hits_peak)
                snap["dwell_sightings"] = _dwell_sightings
                snap["pest"] = max(int(snap.get("pest", 0) or 0), pest_count)
                sid = active_station_id
                print(f"🚨 Live alert lettuce {sid}: {total_hits} hits, pest={pest_severity}, "
                      f"fungus={fungus_severity} (need {SPRAY_MIN_HITS} or either=high)")
                threading.Thread(
                    target=apply_severity_actions, args=(sid, snap), daemon=True
                ).start()

            now = time.time()
            for name, conf in [( _norm_name(d[4]), d[5]) for d in dets if len(d) >= 6]:
                key = f"{active_station_id}_{name}"
                if now - last_log.get(key, 0) > LOG_INTERVAL:
                    # BUG FIX: this used to log every row (pest AND fungus)
                    # with the same merged "severity" — so a whiteflies row
                    # could get stamped "HIGH" just because fungus happened
                    # to be bad that frame, and vice versa. Each row now
                    # gets its own track's severity.
                    row_sev = fungus_severity if name == "fungus" else pest_severity
                    log_detection(active_station_id, name, conf, "hailo", active_profile,
                                  row_sev, fungus_cov)
                    last_log[key] = now

            if dets:
                print(f"🎯 Lettuce {active_station_id} [{current_state}]: "
                      f"{len(dets)} dets → {desc}")
            if time.monotonic() - last_diagnostic >= 10.0:
                last_diagnostic = time.monotonic()
                print("Detection diagnostics: " + json.dumps({
                    "scores": _infer_debug.get("class_scores", {}),
                    "accepted_before_crop_merge": _infer_debug.get("accepted_boxes", {}),
                    "confirmed": _infer_debug.get("confirmed_boxes", {}),
                }))

        except Exception as e:
            print(f"Detection loop error: {e}")
            traceback.print_exc()
        time.sleep(DETECTION_INTERVAL)

# ---------------------------------------------------------------------------
# Buzzer / Pump / SMS stubs (keep your original implementations if needed)
# ---------------------------------------------------------------------------
def init_buzzer():
    if HAS_GPIO:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(BUZZER_PIN, GPIO.OUT)
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            print("🔔 Local GPIO buzzer ready (ESP32 buzzer is primary)")
        except Exception as e:
            print(f"Buzzer init error: {e}")

def _buzzer_off():
    if HAS_GPIO:
        try:
            GPIO.output(BUZZER_PIN, GPIO.LOW)
        except Exception:
            pass
    threading.Thread(target=lambda: esp32_cmd("BUZZ_OFF"), daemon=True).start()


def esp32_cmd(command, timeout=3.0):
    """POST a text command to the ESP32 /cmd endpoint (non-blocking jobs on ESP)."""
    url = f"http://{ESP32_IP}:{ESP32_PORT}/cmd"
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=command.encode("utf-8"),
            headers={"Content-Type": "text/plain"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            print(f"📡 ESP32 {command[:48]} → {body}")
            body = body.strip()
            return body if body == "OK" or body.startswith("OK:") else None
    except Exception as e:
        print(f"⚠ ESP32 '{command[:32]}' failed ({url}): {e}")
        return None


def esp32_cmd_async(command):
    threading.Thread(target=esp32_cmd, args=(command,), daemon=True).start()


def init_pump():
    print(f"💧 Pump/SMS via ESP32 at http://{ESP32_IP}:{ESP32_PORT}")
    print(f"   Farmer SMS → {FARMER_PHONE}  (SIM800L SIM {SIM800L_PHONE})")
    threading.Thread(target=_probe_esp32, daemon=True).start()
    threading.Thread(target=lambda: esp32_cmd("PUMP_OFF"), daemon=True).start()


def _probe_esp32():
    time.sleep(2)
    try:
        import json
        import urllib.request
        url = f"http://{ESP32_IP}:{ESP32_PORT}/status"
        with urllib.request.urlopen(url, timeout=3) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        print(f"📡 ESP32 STATUS (raw) → {raw}")
        try:
            data = json.loads(raw)
            sim_ok = "Ready" if data.get("sim") else "Not ready"
            sms_readable = data.get("sms_result_readable", data.get("sms_result", "unknown"))
            sim_readable = data.get("sim_error_readable") or "No error"
            print("📡 ESP32 STATUS (readable):")
            print(f"    Pump        : {'ON' if data.get('pump') else 'OFF'}")
            print(f"    SIM/GSM     : {sim_ok} — {sim_readable}")
            print(f"    Last SMS    : {sms_readable}")
            print(f"    SMS queued  : {data.get('sms_queued', 0)}")
            print(f"    ESP32 IP    : {data.get('ip', '?')}")
            print(f"    WiFi signal : {data.get('rssi', '?')} dBm")
        except Exception as parse_err:
            print(f"⚠ Could not parse ESP32 status JSON: {parse_err}")
    except Exception as e:
        print(f"⚠ ESP32 not reachable at {ESP32_IP}:{ESP32_PORT} ({e})")
        print("   Set ESP32_IP to the address printed on the ESP32 serial / SMS.")


def send_sms_alert(alert_type, station, message, force=False):
    print(f"📱 SMS [{alert_type}] Lettuce {station}: {message}")
    now = time.time()
    with sms_lock:
        cooldown_key = "manual" if alert_type == "MANUAL_PUMP" else "global"
        last = sms_cooldown.get(cooldown_key)
        enforce_cooldown = alert_type == "MANUAL_PUMP" or (not force and alert_type != "PUMP")
        if enforce_cooldown and last is not None and now - last < SMS_COOLDOWN_SECS:
            print("📱 SMS skipped (cooldown)")
            return
        sms_cooldown[cooldown_key] = now
    # SIM800L GSM alphabet — strip unicode dashes/emoji
    raw = f"FarmCam L{station} {alert_type}: {message}"
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in raw)
    text = " ".join(text.split())[:140]
    print(f"📱 SMS payload: {text}")
    ok = None
    # An unacknowledged HTTP request may already have queued the SMS.
    # Never retry manual notifications and risk sending duplicates.
    attempts = 1 if alert_type == "MANUAL_PUMP" else 3
    for attempt in range(attempts):
        ok = esp32_cmd("SMS:" + text, timeout=15.0)
        if ok is not None:
            print(f"📱 SMS queued on ESP32 ({ok})")
            return
        if attempt + 1 < attempts:
            print(f"📱 SMS retry {attempt + 1}/{attempts}")
            time.sleep(1.0)
    print("📱 SMS FAILED — ESP32/SIM800L did not accept the command")


def _pump_worker(seconds, station, reason, send_sms_after=True):
    global pump_active
    with pump_lock:
        if pump_active:
            print("💧 Pump already running — skip")
            return
        pump_active = True
    started = False
    try:
        print(f"💧 PUMP ON {seconds:.0f}s @ lettuce {station} ({reason})")
        ok = None
        for attempt in range(5):
            ok = esp32_cmd("PUMP_ON", timeout=8.0)
            if ok is not None:
                break
            print(f"💧 PUMP_ON retry {attempt + 1}/5")
            time.sleep(0.4)
        if ok is None:
            print("💧 PUMP_ON failed after retries")
            return
        started = True
        try:
            with open(LAST_PUMP_FILE, "w") as f:
                f.write(datetime.now().isoformat())
        except Exception:
            pass
        time.sleep(seconds)
    finally:
        for attempt in range(5):
            off = esp32_cmd("PUMP_OFF", timeout=4.0)
            if off is not None:
                break
            print(f"💧 PUMP_OFF retry {attempt + 1}/5")
            time.sleep(0.3)
        with pump_lock:
            pump_active = False
        print("💧 PUMP OFF")
        # Send SMS confirming the pump fired
        if send_sms_after and started:
            sms_text = f"Sprayed {seconds:.0f}s ({reason})"
            threading.Thread(
                target=send_sms_alert,
                args=("PUMP", station, sms_text, True),
                daemon=True,
            ).start()


_last_actuation = {}
_live_alerted = {}
# station -> severity level ("low"/"medium"/"high") already sprayed for
# during the current visit. A dict (not a set) so a later escalation in the
# same visit (e.g. sprayed at "medium", then whiteflies build up to "high")
# can still fire one more time, instead of being locked out for the visit.
_sprayed_this_visit = {}


def apply_severity_actions(station, summary):
    """Fire buzzer / pump / SMS from the worst PEST reading and the worst
    FUNGUS reading seen in the 30+30 window — evaluated independently."""
    pest_sev = summary.get("pest_severity") or summary.get("severity", "none")  # back-compat for old-shaped snaps
    fungus_sev = summary.get("fungus_severity", "none")
    sev = _worse_severity(pest_sev, fungus_sev)  # combined, for logging/rank-tracking only
    desc = summary.get("desc", "")
    has_fungus = summary.get("fungus", 0) > 0 or "fungus" in desc.lower()
    has_pest = summary.get("pest", 0) > 0

    print(f"⚖  Lettuce {station} window result: pest={pest_sev.upper()} fungus={fungus_sev.upper()} — {desc}")
    hits = int(summary.get("hits") or 0)
    sightings = int(summary.get("dwell_sightings") or 0)

    # BUG FIX: this used to be a hard "hits >= SPRAY_MIN_HITS or bail" gate.
    # "hits" only ever meant "2+ pest/fungus boxes in the SAME single video
    # frame" — fine for a static fungus patch or a slow worm, but whiteflies
    # are small and move constantly, so getting 2 of them confirmed in one
    # exact frame is rare even during a real infestation. Meanwhile the
    # dashboard's severity (HIGH/MEDIUM) is exactly what farmers look at to
    # decide "should this have sprayed?" — so we now trust that same signal
    # here instead of a stricter, invisible-to-the-user co-occurrence rule.
    # Checked on EACH track independently — a fungus-only HIGH must spray
    # just as reliably as a pest-only HIGH, not just when both line up.
    should_spray = (
        hits >= SPRAY_MIN_HITS
        or pest_sev == "high" or fungus_sev == "high"
        or (sev == "medium" and sightings >= SPRAY_MIN_HITS * 3)
    )
    if not should_spray:
        print(f"💧 Not enough evidence yet (hits={hits}, sightings={sightings}, "
              f"pest={pest_sev}, fungus={fungus_sev}) — no spray")
        return

    prior_sev = _sprayed_this_visit.get(station)
    if prior_sev is not None and _SEV_ORDER.get(sev, 0) <= _SEV_ORDER.get(prior_sev, 0):
        print(f"💧 Already sprayed lettuce {station} this visit at {prior_sev.upper()} "
              f"(now {sev.upper()}, not worse) — no extra pesticide")
        return
    now = time.time()
    if now - _last_actuation.get(station, 0) < PUMP_COOLDOWN:
        print(f"⏳ Actuation cooldown {PUMP_COOLDOWN}s on lettuce {station} — skip")
        return
    _last_actuation[station] = now
    _sprayed_this_visit[station] = sev

    # Any 1+ pest or fungus: pump 2.5s, then buzz (only with pump), then SMS.
    # Buzzer choice checks each track on its own — a fungus-only reading
    # that reaches HIGH now correctly gets the critical tone too, instead
    # of only pest being able to trigger it.
    buzz = "BUZZ_CRITICAL" if (pest_sev == "high" or fungus_sev == "high") else (
        "BUZZ_FUNGUS" if has_fungus and not has_pest else "BUZZ_ALERT"
    )
    print(f"💧 Buzz only with pump — pest={pest_sev} fungus={fungus_sev} lettuce {station}")
    # send_sms_after=True inside _pump_worker will send pump confirmation SMS
    threading.Thread(
        target=_pump_worker, args=(2.5, station, sev.upper(), True), daemon=True
    ).start()
    time.sleep(0.2)
    esp32_cmd_async("BUZZ_SPRAY")  # 2.5s beep with the pump only
    # Send the severity/detection SMS separately
    threading.Thread(
        target=send_sms_alert, args=(sev.upper(), station, desc), daemon=True
    ).start()

# ---------------------------------------------------------------------------
# WiFi helper (simplified)
# ---------------------------------------------------------------------------
def register_farmer_hotspot():
    """Join FarmCam hotspot and mark it autoconnect for headless reboot."""
    print(f"📶 Target hotspot: {TARGET_SSID}")
    try:
        out = subprocess.check_output(["iwgetid", "-r"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        out = ""
    if out == TARGET_SSID:
        print(f"📶 Already on {TARGET_SSID}")
        return
    cmds = [
        ["nmcli", "dev", "wifi", "connect", TARGET_SSID, "password", TARGET_PASS],
        ["nmcli", "connection", "modify", TARGET_SSID, "connection.autoconnect", "yes"],
        ["nmcli", "connection", "modify", TARGET_SSID, "connection.autoconnect-priority", "100"],
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=False, timeout=25,
                           stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        except Exception as e:
            print(f"📶 {' '.join(cmd[:4])} … {e}")
    try:
        out = subprocess.check_output(["iwgetid", "-r"], text=True, stderr=subprocess.DEVNULL).strip()
        print(f"📶 WiFi now: {out or '(not associated)'}")
    except Exception:
        print("📶 Could not read current SSID — use setup_farmcam_wifi.sh once with sudo")

def get_ip_address():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ---------------------------------------------------------------------------
# FastAPI routes (main app)
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "FarmCam online", "models": {
        "seg": seg_model_loaded, "det": det_model_loaded
    }}

@app.get("/api/stream")
async def api_stream():
    return StreamingResponse(generate_mjpeg(),
                             media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/stations")
async def api_stations():
    return list(stations_data.values())

@app.get("/api/system")
async def api_system():
    uptime = int(time.time() - start_time)
    return {
        "build": SERVER_BUILD,
        "npu_active": model is not None,
        "seg_loaded": seg_model_loaded,
        "det_loaded": det_model_loaded,
        "comb_loaded": comb_model_loaded,
        "camera_active": camera is not None,
        "inference_error": _infer_debug["last_err"],
        "inference_frames": _infer_debug["frames"],
        "uptime": f"{uptime//3600}h {(uptime%3600)//60}m",
        "active_station": active_station_id,
        "buzzer_muted": buzzer_muted,
        "stations": list(stations_data.values()),
        "state": current_state,
        "profile": active_profile,
        "hailo_mode": HAILO_MODE,
        "active_hefs": [m["label"] for m in hailo_models],
        "seg_mask_export_ok": (
            None if _infer_debug["seg_dets_seen"] < 20
            else _infer_debug["seg_masks_seen"] > 0
        ),
        "seg_dets_seen": _infer_debug["seg_dets_seen"],
        "seg_masks_seen": _infer_debug["seg_masks_seen"],
    }


@app.get("/api/detection/debug")
async def api_detection_debug():
    return {
        "build": SERVER_BUILD,
        "small_object_tiles": SMALL_OBJECT_TILES,
        "confidence_thresholds": PER_CLASS_THRESHOLDS,
        "minimum_box_area": PER_CLASS_MIN_BOX_AREA,
        "models": [{"label": m["label"], "path": m["path"], "classes": m["class_names"]}
                   for m in hailo_models],
        "class_scores": _infer_debug.get("class_scores", {}),
        "accepted_before_crop_merge": _infer_debug.get("accepted_boxes", {}),
        "confirmed": _infer_debug.get("confirmed_boxes", {}),
        "regions_per_model": _infer_debug.get("regions_per_model", 0),
        "output_shapes": {key: _infer_debug.get(key, "")
                          for key in ("seg_shapes", "det_shapes", "comb_shapes")},
        "error": _infer_debug["last_err"],
    }


MODEL_ALIASES = {
    "COMB": ["COMB"], "BEST": ["COMB"], "COMBINED": ["COMB"],
    "SEG": ["SEG"], "FUNGUS": ["SEG"],
    "DET": ["DET"], "PEST": ["DET"], "DETECT": ["DET"],
    "SPLIT": ["DET", "SEG"],
}


def switch_hef(name: str):
    """Manually load another HEF. Unloads the current one first."""
    key = (name or "").strip().upper().replace(".HEF", "").replace("BEST_", "")
    if key in ("SEG", "DETECT"):
        labels = ["SEG"] if key == "SEG" else ["DET"]
    else:
        labels = MODEL_ALIASES.get(key)
    if not labels:
        return {"ok": False, "error": f"unknown model '{name}'", "options": list(MODEL_ALIASES)}
    print(f"🖱  Manual HEF switch → {labels}")
    ok = hailo_load_labels(labels)
    if not ok and labels != ["COMB"]:
        ok = hailo_load_labels(["COMB"])
        return {"ok": ok, "active": [m["label"] for m in hailo_models], "note": "fallback"}
    return {"ok": ok, "active": [m["label"] for m in hailo_models]}


@app.get("/api/models")
async def api_models():
    have = [c[0] for c in hailo_catalog]
    return {
        "mode": HAILO_MODE,
        "available": have,
        "active": [m["label"] for m in hailo_models],
        "buttons": ["COMB", "DET", "SEG", "SPLIT"],
    }


@app.post("/api/model/{name}")
@app.get("/api/model/{name}")
async def api_switch_model(name: str):
    return switch_hef(name)


def _station_payload(sid):
    sd = stations_data.get(sid, {})
    raw = sd.get("status", "clear") or "clear"
    sev = sd.get("severity_level", "none") or "none"  # legacy combined field, back-compat only
    pest_sev = sd.get("pest_severity_level", "none") or "none"
    fungus_sev = sd.get("fungus_severity_level", "none") or "none"
    is_active = sid == active_station_id
    infer = current_state in ("DETECT_A", "DETECT_B", "DETECTING")
    # Only the current plant is "checking", and only if nothing found yet
    is_checking = is_active and infer and raw == "clear" and sev in ("none", "", None)
    if raw == "clear":
        label = "Checking" if is_checking else "Clear"
        health = "checking" if is_checking else "healthy"
    elif raw == "both":
        label = "Pest & Fungus"
        health = "infected"
    elif raw == "fungus":
        label = "Fungus"
        health = "infected"
    elif raw == "pest":
        label = "Pest"
        health = "infected"
    else:
        label = raw.capitalize()
        health = "infected"
    # Apps that only understand clear/checking/pest/fungus still get a usable status
    app_status = raw if raw in ("clear", "pest", "fungus", "both") else "pest"
    if is_checking:
        app_status = "checking"
    hist = get_history(sid, 25)
    return {
        "id": sid,
        "lettuce_id": sid,
        "name": sd.get("name", f"Lettuce {sid}"),
        "status": app_status,
        "state": app_status,
        "label": label,
        "health": health,
        "infected": health == "infected",
        "checking": is_checking,
        "is_checking": is_checking,
        "confidence": sd.get("confidence", 0.0),
        "last_check": sd.get("last_check") or datetime.now().isoformat(),
        "pest_count": sd.get("pest_count", 0),
        "fungus_count": sd.get("fungus_count", 0),
        "severity_level": sev,                 # legacy combined (worst-of-both) — back-compat only
        "pest_severity_level": pest_sev,       # independent pest (whiteflies/worm) severity
        "fungus_severity_level": fungus_sev,   # independent fungus severity
        "fungus_coverage_pct": sd.get("fungus_coverage_pct", 0.0),
        "pest_types": sd.get("pest_types", []),
        "infestation_desc": sd.get("infestation_desc", ""),
        "active": is_active,
        "whitefly_count": sd.get("whitefly_count", 0),
        "worm_count": sd.get("worm_count", 0),
        "live_detections": sd.get("detections", []),
        "detections": hist,
        "history": hist,
        "detection_history": hist,
        "logs": hist,
        "records": hist,
        "items": hist,
        "data": hist,
        "threat": sd.get("status", "clear"),
    }


# Mobile app compatibility routes (phone polls these every ~1s)
@app.get("/api/status")
async def api_status():
    uptime = int(time.time() - start_time)
    return {
        "build": SERVER_BUILD,
        "status": "ok",
        "online": True,
        "npu_active": model is not None,
        "seg_loaded": seg_model_loaded,
        "det_loaded": det_model_loaded,
        "comb_loaded": comb_model_loaded,
        "camera_active": camera is not None,
        "inference_error": _infer_debug["last_err"],
        "inference_frames": _infer_debug["frames"],
        "uptime": f"{uptime//3600}h {(uptime%3600)//60}m",
        "active_station": active_station_id,
        "state": current_state,
        "buzzer_muted": buzzer_muted,
        "pump_active": pump_active,
        "profile": active_profile,
        "stations": [_station_payload(i) for i in range(1, 5)],
        "lettuce": [_station_payload(i) for i in range(1, 5)],
        "lettuces": [_station_payload(i) for i in range(1, 5)],
        "recent": list(recent_events)[:12],
        "logs": list(recent_events)[:12],
    }


@app.get("/api/lettuce")
@app.get("/api/lettuces")
async def api_lettuce():
    # list form — phone dashboard maps this directly
    return [_station_payload(i) for i in range(1, 5)]


@app.get("/api/lettuce/{sid}")
async def api_lettuce_one(sid: int):
    return _station_payload(sid)


@app.get("/api/dashboard")
async def api_dashboard():
    return {
        "active_station": active_station_id,
        "state": current_state,
        "stations": [_station_payload(i) for i in range(1, 5)],
        "recent": list(recent_events)[:20],
    }


@app.get("/api/logs")
@app.get("/api/detections")
@app.get("/api/history")
async def api_logs(lettuce_id: int = Query(None), station: int = Query(None), id: int = Query(None)):
    sid = lettuce_id or station or id
    rows = get_history(sid, 40)
    # many Flutter clients do snapshot.data as List
    return rows


@app.get("/api/logs/{sid}")
@app.get("/api/detections/{sid}")
@app.get("/api/history/{sid}")
@app.get("/api/lettuce/{sid}/logs")
@app.get("/api/lettuce/{sid}/history")
@app.get("/api/lettuce/{sid}/detections")
async def api_logs_for(sid: int):
    return get_history(sid, 40)


@app.get("/api/pump/status")
async def api_pump_status():
    last_pump = None
    try:
        if os.path.exists(LAST_PUMP_FILE):
            with open(LAST_PUMP_FILE) as f:
                last_pump = f.read().strip() or None
    except Exception:
        last_pump = None
    return {
        "active": pump_active,
        "running": pump_active,
        "last_pump": last_pump,
        "cooldown_secs": PUMP_COOLDOWN,
        "duration": PUMP_DURATION,
        "esp32_ip": ESP32_IP,
    }


@app.post("/api/pump/trigger")
async def api_pump_trigger():
    """One-shot pump trigger (2.5s spray at current station)."""
    sid = active_station_id
    threading.Thread(
        target=_pump_worker, args=(2.5, sid, "MANUAL_TRIGGER", True), daemon=True
    ).start()
    return {"ok": True, "active": pump_active, "station": sid, "message": "pump trigger accepted"}


# ─── Manual pump hold/release (app hold-to-spray button) ─────────

_manual_pump_start_time = 0.0

def _manual_pump_watchdog(generation):
    """Auto-stop manual pump if heartbeat stops (safety), and keep ESP32 watchdog refreshed."""
    global manual_pump_on, pump_active
    last_esp_ping = time.time()
    while True:
        time.sleep(0.25)
        with manual_control_lock:
            if not manual_pump_on or generation != manual_pump_generation:
                return
            now = time.time()
            stop = now - manual_pump_heartbeat > MANUAL_PUMP_TIMEOUT
            if not stop and now - last_esp_ping > 1.0:
                last_esp_ping = now
                stop = esp32_cmd("PUMP_HEARTBEAT", timeout=1.0) != "OK:alive"
            if stop:
                print("💧 Manual pump heartbeat/device connection lost — stopping")
                manual_pump_on = False
                esp32_cmd("PUMP_OFF", timeout=2.0)
                with pump_lock:
                    pump_active = False
                return


@app.post("/api/pump/hold")
def api_pump_hold():
    """Start pump and keep it on until /api/pump/release is called.
    The app sends heartbeats to /api/pump/heartbeat while button is held.
    If heartbeats stop for >2s, the pump auto-stops (safety)."""
    global manual_pump_on, manual_pump_heartbeat, pump_active, _manual_pump_start_time, manual_pump_generation
    # Sync routes run in FastAPI's thread pool so modem HTTP waits cannot
    # block incoming app heartbeats. Serialize start/stop with the watchdog.
    with manual_control_lock:
        if manual_pump_on:
            manual_pump_heartbeat = time.time()
            return {"ok": True, "active": True, "already_on": True}
        with pump_lock:
            if pump_active:
                return JSONResponse(status_code=409, content={"ok": False, "error": "Pump is already running"})
            pump_active = True
        ok = esp32_cmd("PUMP_HOLD", timeout=2.0)
        if ok != "OK":
            # An acknowledgement may have been lost; cancel any accepted hold.
            esp32_cmd("PUMP_OFF", timeout=1.0)
            with pump_lock:
                pump_active = False
            return JSONResponse(status_code=503, content={
                "ok": False, "active": False,
                "error": f"ESP32 at {ESP32_IP} did not acknowledge PUMP_HOLD. Check its IP and Wi-Fi.",
            })
        manual_pump_on = True
        manual_pump_heartbeat = time.time()
        _manual_pump_start_time = time.time()
        manual_pump_generation += 1
        try:
            with open(LAST_PUMP_FILE, "w") as f:
                f.write(datetime.now().isoformat())
        except Exception:
            pass
        threading.Thread(target=_manual_pump_watchdog, args=(manual_pump_generation,), daemon=True).start()
        threading.Thread(
            target=send_sms_alert,
            args=("MANUAL_PUMP", active_station_id, "Manual spray started via app"),
            daemon=True,
        ).start()
    print("💧 Manual pump HOLD started")
    return {"ok": True, "active": True}


@app.post("/api/pump/release")
def api_pump_release():
    """Stop the pump (manual hold button released)."""
    global manual_pump_on, pump_active
    with manual_control_lock:
        was_on = manual_pump_on
        duration = max(0.0, time.time() - _manual_pump_start_time) if was_on else 0.0
        manual_pump_on = False
        off = esp32_cmd("PUMP_OFF", timeout=2.0)
        with pump_lock:
            pump_active = False
    print(f"💧 Manual pump RELEASED (ran {duration:.1f}s)")
    if off != "OK":
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "Stop not acknowledged by ESP32; hardware watchdog will stop the pump.",
        })
    return {"ok": True, "active": False, "duration": round(duration, 1)}


@app.post("/api/pump/heartbeat")
async def api_pump_heartbeat():
    """Keep-alive for manual pump hold. Must be called every 500ms."""
    global manual_pump_heartbeat
    manual_pump_heartbeat = time.time()
    return {"ok": True, "active": manual_pump_on}

# ---------------------------------------------------------------------------
# Viewer app (port 8080)
# ---------------------------------------------------------------------------
viewer_app = FastAPI()

VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FarmCam Live</title>
<style>
:root {
  --bg: #0f172a; --bg-card: #1e293b; --border: #334155;
  --text: #f1f5f9; --text-muted: #94a3b8;
  --green: #10b981; --red: #ef4444; --amber: #f59e0b; --blue: #3b82f6;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }
.header { padding: 16px; background: var(--bg-card); border-bottom: 1px solid var(--border); }
.logo { font-size: 1.4em; font-weight: 800; }
.main { max-width: 1100px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
.stream-card { background: var(--bg-card); border-radius: 16px; padding: 6px; border: 1px solid var(--border); }
.stream-wrap { aspect-ratio: 4/3; background: #000; border-radius: 12px; overflow: hidden; }
.stream-wrap img { width: 100%; height: 100%; object-fit: contain; }
.summary-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }
.summary-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 14px; text-align: center; }
.val { font-size: 1.8em; font-weight: 800; }
.stations-grid { display: grid; grid-template-columns: repeat(2,1fr); gap: 10px; }
@media(min-width:700px){ .stations-grid{ grid-template-columns: repeat(4,1fr);} }
.station-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
.station-card.status-pest { border-left: 3px solid var(--red); }
.station-card.status-fungus { border-left: 3px solid var(--amber); }
.station-card.status-clear { border-left: 3px solid var(--green); }
.hef-row { display:flex; flex-wrap:wrap; gap:8px; }
.hef-btn { background:#334155; color:#f1f5f9; border:1px solid #475569; border-radius:10px; padding:10px 14px; font-weight:700; cursor:pointer; }
.hef-btn.on { background:#10b981; color:#042f1e; }
#hefStatus { color:#94a3b8; font-size:0.9em; }
</style>
</head>
<body>
<div class="header"><div class="logo">FarmCam Live</div></div>
<div class="main">
  <div class="stream-card"><div class="stream-wrap">
    <img id="stream" src="/stream" alt="Live">
  </div></div>
  <div>
    <div style="margin-bottom:8px;font-weight:700">Switch HEF</div>
    <div class="hef-row">
      <button class="hef-btn" data-hef="COMB" onclick="setHef('COMB')">best.hef (all)</button>
      <button class="hef-btn" data-hef="DET" onclick="setHef('DET')">best_detect.hef</button>
      <button class="hef-btn" data-hef="SEG" onclick="setHef('SEG')">best_seg.hef</button>
      <button class="hef-btn" data-hef="SPLIT" onclick="setHef('SPLIT')">seg + detect</button>
    </div>
    <div id="hefStatus">active: …</div>
  </div>
  <div class="summary-row">
    <div class="summary-card"><div class="val" id="totalPests" style="color:var(--red)">0</div><div>Pests</div></div>
    <div class="summary-card"><div class="val" id="totalFungus" style="color:var(--amber)">0</div><div>Fungus</div></div>
    <div class="summary-card"><div class="val" id="clearCount" style="color:var(--green)">4</div><div>Clear</div></div>
  </div>
  <div class="stations-grid" id="stationsGrid"></div>
</div>
<script>
async function refresh() {
  try {
    const res = await fetch('/api/stations');
    const stations = await res.json();
    let p=0, f=0, c=0;
    document.getElementById('stationsGrid').innerHTML = stations.map(s => {
      p += s.pest_count||0; f += s.fungus_count||0;
      if (s.status==='clear') c++;
      return `<div class="station-card status-${s.status}">
        <b>Lettuce ${s.id}</b><br>
        ${s.status} · pests:${s.pest_count||0} fungus:${s.fungus_count||0}
      </div>`;
    }).join('');
    document.getElementById('totalPests').textContent = p;
    document.getElementById('totalFungus').textContent = f;
    document.getElementById('clearCount').textContent = c;
  } catch(e){}
}
async function setHef(name){
  document.getElementById('hefStatus').textContent = 'switching to '+name+' …';
  try {
    const r = await fetch('/api/model/'+name, {method:'POST'});
    const j = await r.json();
    document.getElementById('hefStatus').textContent = j.ok
      ? ('active: '+(j.active||[]).join(', '))
      : ('failed: '+(j.error||'unknown'));
    markHef(j.active||[]);
  } catch(e){
    document.getElementById('hefStatus').textContent = 'switch failed';
  }
}
function markHef(active){
  document.querySelectorAll('.hef-btn').forEach(b=>{
    b.classList.toggle('on', active.includes(b.dataset.hef) || (b.dataset.hef==='COMB' && active.includes('COMB')));
  });
}
async function loadHef(){
  try {
    const r = await fetch('/api/models');
    const j = await r.json();
    document.getElementById('hefStatus').textContent = 'active: '+(j.active||[]).join(', ');
    markHef(j.active||[]);
  } catch(e){}
}
refresh(); loadHef(); setInterval(refresh, 2500);
</script>
</body>
</html>
"""

@viewer_app.get("/")
async def viewer_page():
    return Response(content=VIEWER_HTML, media_type="text/html")

@viewer_app.get("/stream")
async def viewer_stream():
    return StreamingResponse(generate_mjpeg(),
                             media_type="multipart/x-mixed-replace; boundary=frame")

@viewer_app.get("/stream-raw")
async def viewer_stream_raw():
    return StreamingResponse(generate_raw_mjpeg(),
                             media_type="multipart/x-mixed-replace; boundary=frame")

@viewer_app.get("/api/models")
async def viewer_api_models():
    return await api_models()

@viewer_app.post("/api/model/{name}")
@viewer_app.get("/api/model/{name}")
async def viewer_api_switch_model(name: str):
    return switch_hef(name)

@viewer_app.get("/api/stations")
async def viewer_api_stations():
    result = []
    for sid in range(1, 5):
        sd = stations_data.get(sid, {})
        result.append({
            "id": sid,
            "status": sd.get("status", "clear"),
            "pest_count": sd.get("pest_count", 0),
            "fungus_count": sd.get("fungus_count", 0),
            "last_check": sd.get("last_check"),
            "active": sid == active_station_id,
        })
    return result

@viewer_app.get("/api/system")
async def viewer_api_system():
    uptime = int(time.time() - start_time)
    return {
        "npu_active": model is not None,
        "seg_loaded": seg_model_loaded,
        "det_loaded": det_model_loaded,
        "comb_loaded": comb_model_loaded,
        "camera_active": camera is not None,
        "uptime": f"{uptime//3600}h {(uptime%3600)//60}m",
        "active_station": active_station_id,
    }

def start_viewer_server():
    uvicorn.run(viewer_app, host="0.0.0.0", port=VIEWER_PORT, log_level="warning")

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def acquire_instance_lock():
    """Hold a machine-wide Linux lock before touching camera, Hailo or ESP32."""
    global instance_lock_file
    if os.name != "posix":
        return
    import fcntl
    lock_path = Path("/tmp/farmcam-server.lock")
    # Exclusive creation works for both root service and manual unprivileged runs.
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        os.fchmod(fd, 0o666)
        handle = os.fdopen(fd, "r+")
    except FileExistsError:
        handle = open(lock_path, "r+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        owner = handle.read().strip() or "unknown"
        handle.close()
        raise RuntimeError(f"FarmCam is already running (PID {owner}). "
                           "Use sudo systemctl restart farmcam, or stop the service before a manual run.")
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    instance_lock_file = handle


def cleanup():
    coordinator.shutdown()   # graceful teardown of new subsystems
    print("\n🛑 Shutting down FarmCam...")
    global camera, instance_lock_file
    stop_event.set()
    for worker in worker_threads:
        worker.join(timeout=3.0)
    with hailo_swap_lock:
        hailo_release_device()
    if camera is not None:
        try:
            camera.stop()
            camera.close()
        except Exception as e:
            print(f"⚠ Camera cleanup: {e}")
        camera = None
    if HAS_GPIO:
        try:
            GPIO.cleanup()
        except Exception:
            pass
    if instance_lock_file is not None:
        instance_lock_file.close()
        instance_lock_file = None

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def startup():
    print("=" * 60)
    print("  🌿 FarmCam Server v2.1 (Fixed Dual-Hailo)")
    print("  Lettuce Pest & Fungus Detection")
    print(f"  Build: {SERVER_BUILD}; small-object crops: {SMALL_OBJECT_TILES}")
    print("=" * 60)

    acquire_instance_lock()
    init_camera()
    init_hailo_models()          # <-- FIXED dual model loader
    if not hailo_models:
        raise RuntimeError("No Hailo models loaded; detection cannot start. "
                           "Error 74 means no free Hailo device. Check other FarmCam/Hailo "
                           "processes and run hailortcli scan before restarting.")
    init_buzzer()
    init_pump()
    init_db()
    coordinator.wire(app)   # initialise new subsystems + register API extensions

    cam_thread = threading.Thread(target=capture_frames, daemon=True)
    worker_threads.append(cam_thread)
    cam_thread.start()

    detect_thread = threading.Thread(target=detect_loop, daemon=True)
    worker_threads.append(detect_thread)
    detect_thread.start()

    register_farmer_hotspot()

    viewer_thread = threading.Thread(target=start_viewer_server, daemon=True)
    viewer_thread.start()

    ip = get_ip_address()
    print(f"\n🚀 Servers starting...")
    print(f"   📱 App API:     http://{ip}:{SERVER_PORT}")
    print(f"   📺 Live Viewer: http://{ip}:{VIEWER_PORT}")
    print(f"   🎥 Stream:      http://{ip}:{SERVER_PORT}/api/stream")
    print(f"   Models loaded → SEG: {seg_model_loaded}  DET: {det_model_loaded}")
    print()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import atexit
    atexit.register(cleanup)

    if "--help" in sys.argv:
        print("""
FarmCam Server (Fixed)
  python3 main.py          Normal start (stop farmcam service first)
  python3 main.py --help   Show help
        """)
    else:
        try:
            startup()
            uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
        except (RuntimeError, KeyboardInterrupt) as e:
            print(f"FarmCam stopped: {e}")
            sys.exit(1)

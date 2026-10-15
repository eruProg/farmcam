"""
FarmCam – Central Configuration
================================
All tunables, thresholds, timings, pins, and paths live here.
No magic numbers in any other module. Edit this file to tune behaviour.
Do not import secrets from here; use os.environ via the env vars below.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Scan timing (seconds)
# ---------------------------------------------------------------------------
# Upper-position detection window
DETECT_WINDOW_SECS  = 30.0
# Motor transit down to lower position
TRANSIT_DOWN_SECS   = 5.0
# Motor transit back up to upper position (after DETECT_LOWER)
TRANSIT_UP_SECS     = 5.0
# Inter-station travel time (stations 1-3)
MOVE_SECS           = 10.0
# Travel time after station 4 (longer loop path back to station 1)
LOOP_AFTER_4_SECS   = 15.0
# Discard frames captured within this many seconds of a phase change
SETTLE_SECS         = 1.0

# Cycle time formula (for reference / documentation):
# T_cycle = 4*(DETECT*2 + T_down + T_up) + 3*MOVE + LOOP
#         = 4*(30+30+5+5) + 3*10 + 15 = 325 s ≈ 5 min 25 s

# ---------------------------------------------------------------------------
# Station ROIs — normalized [x1, y1, x2, y2] in 0..1 range
# Run calibrate_roi.py to capture a reference frame and adjust these values.
# A detection is accepted only if its bbox centre falls inside the ROI.
# ---------------------------------------------------------------------------
STATION_ROIS = {
    (1, "upper"): [0.05, 0.05, 0.95, 0.95],
    (1, "lower"): [0.05, 0.05, 0.95, 0.95],
    (2, "upper"): [0.05, 0.05, 0.95, 0.95],
    (2, "lower"): [0.05, 0.05, 0.95, 0.95],
    (3, "upper"): [0.05, 0.05, 0.95, 0.95],
    (3, "lower"): [0.05, 0.05, 0.95, 0.95],
    (4, "upper"): [0.05, 0.05, 0.95, 0.95],
    (4, "lower"): [0.05, 0.05, 0.95, 0.95],
}

# ---------------------------------------------------------------------------
# Confidence thresholds — per class
# ---------------------------------------------------------------------------
PER_CLASS_THRESHOLDS = {
    # Fungus is a segmentation class; diffuse texture → naturally lower scores.
    # Below 0.28 we rely on the mask-quality gate and temporal consensus instead.
    "fungus":     0.28,
    "whitefly":   0.40,
    "whiteflies": 0.40,
    "worm":       0.45,
}
CONFIDENCE_THRESHOLD = 0.35   # fallback for unknown classes

# ---------------------------------------------------------------------------
# Size and shape gates — (area_min_px, area_max_px, ar_min, ar_max)
# Areas are in model-space pixels (640×640).
# Whitefly: 1-3 mm; at ~PX_PER_MM pixels/mm → 20-60 px linear → 400-3600 px².
# ---------------------------------------------------------------------------
PX_PER_MM = 20.0   # calibrate with a ruler at camera working distance

PER_CLASS_SIZE = {
    "whiteflies": (16,    3600, 0.3,  5.0),
    "whitefly":   (16,    3600, 0.3,  5.0),
    "worm":       (150, 40000, 0.1, 12.0),
    "fungus":     (25,  999999, 0.05, 20.0),
}

# Legacy fallbacks (used in main.py frozen path)
MIN_BOX_AREA     = 150
MIN_ASPECT_RATIO = 0.15
MAX_ASPECT_RATIO = 8.0
MAX_DETECTIONS_PER_FRAME = 30
NMS_IOU_THRESHOLD = 0.45
REG_MAX = 16
MIN_MASK_AREA_PX    = 4
MIN_MASK_FILL_RATIO = 0.05
PER_CLASS_MIN_BOX_AREA = {"whiteflies": 16, "whitefly": 16, "fungus": 25}

# ---------------------------------------------------------------------------
# Skin / hand rejection  (YCrCb colour space)
# ---------------------------------------------------------------------------
# Cr range for skin
SKIN_CR_MIN, SKIN_CR_MAX = 133, 173
# Cb range for skin
SKIN_CB_MIN, SKIN_CB_MAX = 77,  127
# Reject a pest bbox if more than this fraction of its pixels are skin-coloured
SIGMA_MAX = 0.30
# If skin-coloured blob covers more than this fraction of the whole frame,
# suppress ALL pest candidates in that frame and log hand_in_frame.
# The cheap check is run on a 160×120 downscale.
SKIN_FRAME_MAX_PCT = 0.12

# ---------------------------------------------------------------------------
# Context gates — whitefly only
# ---------------------------------------------------------------------------
# The ring around the bbox must have at least this green-pixel ratio (HSV h=35-85, s>50)
GREEN_RATIO_MIN = 0.15
# Bbox interior must be bright (pale insects against green leaf)
BRIGHT_V_MIN    = 160   # mean HSV V
# Bbox interior must be low-saturation (white/pale insect body)
DARK_S_MAX      = 80    # mean HSV S

# ---------------------------------------------------------------------------
# Temporal tracker
# ---------------------------------------------------------------------------
# A track must be seen for at least this long before being confirmed
T_MIN_SECS          = 2.0
# Or confirmed in at least K of the last N frames
TRACK_K             = 3
TRACK_N             = 5
# Max allowed centroid drift (pixels per second) — moving fingers fail this
CENTROID_DRIFT_MAX  = 30.0
# IoU threshold for re-matching a track across rescans (persistence merge)
TRACK_IOU_RESCAN    = 0.40

# ---------------------------------------------------------------------------
# Buzzer
# ---------------------------------------------------------------------------
BUZZER_PIN            = 17      # Raspberry Pi BCM GPIO
BUZZER_FREQ_HZ        = 2400    # PWM frequency for passive buzzer
BUZZER_DUTY           = 25      # % duty cycle — reduces volume vs 50%
BUZZER_MUTED          = False   # runtime toggle (overridden by UI)

# Minimum time between any two buzzer patterns
BUZZ_MIN_INTERVAL_S   = 10
# Per-class cooldown (avoids spamming)
BUZZ_CLASS_COOLDOWN_S = 60
# Hard cap: total buzzer ON time per rolling 60-second window
BUZZ_MAX_ON_PER_MIN_S = 5

# Patterns: list of (on_ms, off_ms) tuples.  Max 3 beeps per spec.
BUZZ_PATTERN_PEST    = [(80, 80), (80, 0)]         # 2 × 80 ms — pest confirmed
BUZZ_PATTERN_FUNGUS  = [(200, 0)]                   # 1 × 200 ms — fungus confirmed
BUZZ_PATTERN_FAULT   = [(100, 100), (100, 100), (100, 0)]  # 3 short — system fault

# ---------------------------------------------------------------------------
# Pump
# ---------------------------------------------------------------------------
PUMP_PIN        = 27
PUMP_COOLDOWN_S = 300     # 5 minutes between automatic sprays
PUMP_DURATION_S = 2.5
SPRAY_MIN_HITS  = 2
PUMP_CYCLE_GAP_SECS = 2.0
MANUAL_PUMP_TIMEOUT = 2.0   # auto-off if no heartbeat (seconds)

# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------
SMS_COOLDOWN_S       = 300    # 5 minutes between automatic alerts
SMS_MAX_RETRIES      = 3
SMS_RETRY_BACKOFF_S  = [2, 5, 10]
# !! Read from environment — never hardcode phone numbers in source !!
FARMER_PHONE = os.environ.get("FARMCAM_FARMER_PHONE", "")
SIM_PHONE    = os.environ.get("FARMCAM_SIM_PHONE", "")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
SCRIPT_DIR       = Path(__file__).parent.resolve()
DATA_DIR         = SCRIPT_DIR / "data"
DB_PATH          = str(DATA_DIR / "farmcam.db")
SNAPSHOT_DIR     = DATA_DIR / "snapshots"
REJECTED_DIR     = DATA_DIR / "rejected"
CALIB_DIR        = DATA_DIR / "calibration"
# Snapshot retention — prunes oldest first, never active tracks
RETENTION_MAX_GB   = 4.0
RETENTION_MAX_DAYS = 30

# ---------------------------------------------------------------------------
# ESP32
# ---------------------------------------------------------------------------
ESP32_IP   = os.environ.get("FARMCAM_ESP32_IP", "10.244.166.161")
ESP32_PORT = 80

# ---------------------------------------------------------------------------
# Severity thresholds
# ---------------------------------------------------------------------------
SEV_PEST_HIGH       = 3
SEV_PEST_MEDIUM     = 1
SEV_FUNGUS_HIGH_PCT = 15.0
SEV_FUNGUS_MED_PCT  = 5.0

# ---------------------------------------------------------------------------
# Detection / camera
# ---------------------------------------------------------------------------
DETECTION_INTERVAL    = 0.10    # seconds between detect_loop iterations
LOG_INTERVAL          = 1.0     # minimum seconds between DB log rows for same class
SERVER_PORT           = 8000
VIEWER_PORT           = 8080
TARGET_SSID           = "FarmCam"
TARGET_PASS           = "farmcam1"
SMALL_OBJECT_TILES    = os.environ.get("FARMCAM_SMALL_OBJECT_TILES", "1") == "1"

# Stations scanned in this order
LETTUCE_SEQUENCE = [1, 2, 3, 4]

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
# Upper-position and lower-position detection window (1 minute per part)
DETECT_WINDOW_SECS  = 60.0
# Motor transit down to lower position (15 seconds)
TRANSIT_DOWN_SECS   = 15.0
# Motor transit back up to upper position (15 seconds)
TRANSIT_UP_SECS     = 15.0
# Inter-station travel time between lettuces (10 seconds)
MOVE_SECS           = 10.0
# Travel time after station 4 (loop path back to station 1)
LOOP_AFTER_4_SECS   = 10.0
# Discard frames captured within this many seconds of a phase change
SETTLE_SECS         = 1.0

# Cycle time formula:
# T_cycle = 4*(60 + 60 + 15 + 15 + 2*1) + 3*10 + 10 = 648 s

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
# Which pest (whitefly + worm) HEF is installed in models/best_detect.hef:
#   "old"     - the original model (worms trained on red/blue-swapped photos)
#   "natural" - the model retrained on colour-fixed photos (datasets/pest pt)
# The default is the model pi_update.zip installs, so server files copied by
# hand (without install_update.sh and its service settings) still match it.
# Set FARMCAM_PEST_MODEL=old only when the original HEF is installed.
# ---------------------------------------------------------------------------
PEST_MODEL = os.environ.get("FARMCAM_PEST_MODEL", "natural")
# Same for the fungus (SEG) model in models/best_seg.hef:
#   "old" - trained on red/blue-swapped photos; "natural" - retrained on the
#   colour-fixed new export (datasets/fungus pt); "natural2" - also trained on
#   the real machine photos with a second class "dirt" (fly droppings, soil,
#   dirt in the cup), which the Pi never counts. Default: "natural2", the
#   model pi_update.zip installs; set FARMCAM_FUNGUS_MODEL for an older HEF.
FUNGUS_MODEL = os.environ.get("FARMCAM_FUNGUS_MODEL", "natural2")
FUNGUS_NATURAL = FUNGUS_MODEL in ("natural", "natural2")   # natural colours, retrained
# Class order inside best_seg.hef (index 0, 1, ...).
SEG_CLASS_NAMES = ["fungus", "dirt"] if FUNGUS_MODEL == "natural2" else ["fungus"]
# Classes a model may output that are never pests or fungus (rejected by the verifier).
IGNORED_CLASSES = ("dirt",)
# Crops for the pest (DET) model: 2 = full frame + 2x2 crops (today),
# 3 = full frame + 3x3 smaller crops (whiteflies ~1.4x bigger to the model,
# ~50% more inference time per frame). Fungus always uses 2x2.
WHITEFLY_TILE_GRID = int(os.environ.get("FARMCAM_WF_GRID", "2"))

# ---------------------------------------------------------------------------
# Confidence thresholds — per class
# ---------------------------------------------------------------------------
PER_CLASS_THRESHOLDS = {
    # Chosen with evaluate.py on the labelled Roboflow test splits (full Pi
    # pipeline): the lowest minimum that keeps precision >= 90%, because a
    # false alarm means spraying pesticide for nothing.
    #   fungus   0.40 -> P 90.0%  R 25.0%   (0.35: P 83.7%)
    #   worm     0.65 -> P 98% R 94% on the Roboflow test split, and on 143
    #            newly labelled machine photos (never trained on): P 87% R 72%
    #            (0.50 there gave 14 false worms, mostly dark fungus lesions)
    #   whitefly 0.65 -> on those machine photos the current model found 0 of
    #            7 real whiteflies and 30 false ones (glare) at 0.50; 0.65
    #            leaves 2. Effectively off until the retrained model, which
    #            is better than spraying for glare.
    #   whitefly 0.55 with the retrained "natural" model AS COMPILED for the
    #            Hailo-8L (8-bit, hef_emulator.py), 148 test photos, full
    #            pipeline: 0.65 P 95.2% R 12.0% | 0.60 P 95.6% R 38.9%
    #            | 0.55 P 96.7% R 52.1% | 0.50 P 91.7% R 66.5%
    #            8-bit scores run ~0.05 below the .pt model's (.pt at 0.60:
    #            P 97.6% R 49.1%), so 0.55 on the chip matches 0.60 on the .pt.
    #   fungus   0.30 with the retrained "natural" model AS COMPILED (best_seg.hef,
    #            emulated chip) plus FUNGUS_MIN_PER_FRAME 2, chosen on the valid
    #            photos (28/32 infected found, 1/9 healthy flagged); test photos:
    #            26/34 found, 2/9 flagged. Chip scores run lower than the .pt's.
    #            Kept for "natural2" (v4, fungus + dirt, emulated chip): test
    #            photos 28/34 found, 2/9 flagged; held-out machine photos 0/24
    #            flagged (the v2 HEF: 14/24, mostly fly droppings). Best F1 at 0.25-0.30.
    # Re-check after retraining: new models give different scores.
    "fungus":     0.30 if FUNGUS_NATURAL else 0.40,
    #   whitefly 0.50 with pest v3 (fine-tuned on machine whiteflies) AS
    #            COMPILED: 148 test photos 0.55 P 97.7% R 54.8% | 0.50 P 96.2%
    #            R 65.2%. A real but blurry machine whitefly scored 0.51.
    "whitefly":   0.50 if PEST_MODEL == "natural" else 0.65,
    "whiteflies": 0.50 if PEST_MODEL == "natural" else 0.65,
    "worm":       0.65,
}
CONFIDENCE_THRESHOLD = 0.35   # fallback for unknown classes

# ---------------------------------------------------------------------------
# Size and shape gates — (area_min_px, area_max_px, ar_min, ar_max)
# Areas are in live-frame pixels (1280×960 lores stream) — the verifier
# runs on detect_frame() output, which is already scaled to the frame.
# Whitefly: 1-3 mm; at ~PX_PER_MM pixels/mm → 20-60 px linear → 400-3600 px².
# ---------------------------------------------------------------------------
PX_PER_MM = 20.0   # calibrate with a ruler at camera working distance

PER_CLASS_SIZE = {
    "whiteflies": (16,    3600, 0.3,  5.0),
    "whitefly":   (16,    3600, 0.3,  5.0),
    "worm":       (25, 40000, 0.1, 12.0),
    # Measured on the labelled fungus test split (evaluate.py / tune_checks.py):
    # min 400 kept 13% of lesions, 50 kept 28.5% at the same 84% precision.
    # Real lesions are 8x6-16x16 px; 50 still drops 6x7 glare specks.
    "fungus":     (50, 999999, 0.05, 20.0),
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
PER_CLASS_MIN_BOX_AREA = {"whiteflies": 16, "whitefly": 16, "worm": 25, "fungus": 25}
# Scanning starts from the app (Controls > Start scan), never at boot: the
# motor is started separately and the camera must be in position first.
AUTO_START_SCAN = False

# ---------------------------------------------------------------------------
# Skin / hand rejection  (YCrCb colour space)
# ---------------------------------------------------------------------------
# Cr range for skin
SKIN_CR_MIN, SKIN_CR_MAX = 133, 173
# Cb range for skin
SKIN_CB_MIN, SKIN_CB_MAX = 77,  127
# Skin is never this dark: a dark red wall (Y 40-55) matched the Cr/Cb range and
# was taken for a hand, throwing away a real 86% worm. Hands measure Y 100-160.
SKIN_Y_MIN = 70
# Reject a pest bbox if more than this fraction of its pixels are skin-coloured
SIGMA_MAX = 0.30
# If ONE connected skin-coloured blob inside the station ROI covers more than
# this fraction of the ROI, suppress ALL pest candidates and log hand_in_frame.
# (Counting scattered pixels fired on reddish backgrounds with no hand.)
# The cheap check is run on a 160×120 downscale.
SKIN_FRAME_MAX_PCT = 0.12

# ---------------------------------------------------------------------------
# Context gates — whitefly only
# ---------------------------------------------------------------------------
# The ring around the bbox must have at least this green-pixel ratio (HSV h=35-85, s>50)
GREEN_RATIO_MIN = 0.15
# Enough of the (unpadded) bbox must be pale insect pixels. A mean over the
# box failed real whiteflies because a small box is mostly green leaf.
WF_PALE_V_MIN    = 150   # pixel HSV V at least this bright
WF_PALE_S_MAX    = 90    # pixel HSV S at most this saturated
WF_PALE_FRAC_MIN = 0.05  # fraction of bbox pixels that must be pale
                         # (labelled test: 0.05 -> P 97.8% R 36.3%; 0.10 -> R 34.7%;
                         #  off -> P 87.4%, so the gate does stop false whiteflies)
# A whitefly is brighter than the leaf around it: 90th-percentile V inside the
# box minus the ring's median V. Rejects specks no brighter than the leaf.
WF_CONTRAST_MIN  = 15    # bright lettuce is V~220 and a whitefly ~240

# ---------------------------------------------------------------------------
# Context gates — fungus only
# ---------------------------------------------------------------------------
# The fungus-only SEG model scores worms as fungus. Worm candidates at or above
# WORM_HINT_MIN_CONF (below the counted worm threshold too) cancel a fungus box
# when this fraction of the worm box lies inside it.
WORM_HINT_MIN_CONF  = 0.25
FUNGUS_WORM_OVERLAP = 0.30
# Fungus lives on the leaf: bbox + 16px ring must be at least this green.
# Rejects black/dark objects in the background.
FUNGUS_LEAF_GREEN_MIN = 0.20
# The box itself must be mostly plant (leaf green, or brown/yellow lesion),
# not white casing or pot. Fraction of box pixels.
FUNGUS_INTERIOR_LEAF_MIN = 0.30
# A frame shows fungus only with at least this many lesions passing every
# check. The retrained model's false alarms on healthy lettuce were mostly one
# doubtful spot (shadow, background); valid photos, compiled model, minimum
# 0.30: 1 lesion -> 32/32 infected found, 3/9 healthy flagged; 2 lesions ->
# 28/32 found, 1/9 flagged. (The spray rule already needs 2 hits per frame.)
FUNGUS_MIN_PER_FRAME = 2 if FUNGUS_NATURAL else 1
# A worm must sit on the plant: share of leaf/lesion pixels in the 24 px band
# around its box. Real worms on the pest test photos: 0/365 below 0.25 (1st
# percentile 0.80), so 0.20 only removes "worms" on the background (black
# pipe, wall shadows). False worms ON leaves need retraining, not this check.
WORM_RING_PLANT_MIN = 0.20
# (Tried and dropped: a "must be on the lettuce" region check. The remaining
# false fungus boxes sit on the plant or pot itself, e.g. dark gaps between
# leaves, so it removed none of them; brown walls also look like plant.)
# Glare / water droplets / casing: bright, colourless pixels.
GLARE_V_MIN    = 220
GLARE_S_MAX    = 40
GLARE_FRAC_MAX = 0.50

# Save at most one rejected crop per class+reason per this many seconds.
REJECTED_SAVE_INTERVAL_S = 2.0

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
# One pump run wets all 4 lettuces (shared pipe/nozzles), so the cooldown is
# for the whole machine. A manual spray from the app (confirmed by the user
# during the cooldown) restarts it.
PUMP_COOLDOWN_S = 7 * 86400   # 7 days between automatic sprays
PUMP_DURATION_S = 2.5
# Client rule: spray when one lettuce visit (upper + lower part) has at least
# this many CONFIRMED insects of one type (each seen in 3 of 5 frames / >= 2 s
# by the verifier's tracker, so a one-frame false speck never counts).
SPRAY_WORMS_MIN      = 3
SPRAY_WHITEFLIES_MIN = 3
# Fungus sprays at "high" severity: SEV_FUNGUS_HIGH_PCT of the frame covered.
SPRAY_MIN_HITS  = 2   # legacy; no longer used for spray decisions
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
CAPTURE_DIR      = DATA_DIR / "captures"      # full-resolution dataset photos
ESP32_SAVED_IP   = DATA_DIR / "esp32_ip.json"  # last address the ESP32 was found at

# Second deep-learning stage for whiteflies (wf_classifier.py): a small CNN
# re-checks every whitefly candidate on a sharp crop of the 4056x3040 frame and
# replaces the colour rules (green ring, pale, contrast). Inactive, with
# everything as before, while the model file is missing.
WF_CLS_MODEL  = os.environ.get("FARMCAM_WF_CLS", str(SCRIPT_DIR / "models" / "whitefly_cls.onnx"))
WF_STAGE1_MIN = 0.25    # YOLO minimum for whitefly candidates when the CNN decides
# Classifier cut-off, from the machine test photos (10 real whiteflies, 47 photos)
# on the emulated Hailo-8L with pest v3: colour rules 2/10 found, 2/8 infested
# photos, 1/39 clean flagged | CNN 0.3: 5/10, 6/8, 1/39 | CNN 0.5: 3/10, 4/8, 1/39.
WF_CLS_MIN    = float(os.environ.get("FARMCAM_WF_CLS_MIN", "0.3"))
# Snapshot retention — prunes oldest first, never active tracks
RETENTION_MAX_GB   = 4.0
RETENTION_MAX_DAYS = 30

# ---------------------------------------------------------------------------
# ESP32
# ---------------------------------------------------------------------------
ESP32_IP   = os.environ.get("FARMCAM_ESP32_IP", "10.229.135.161")
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

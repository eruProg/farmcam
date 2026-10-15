"""
calibrate_roi.py — ROI calibration helper
==========================================
Run this on the Pi to capture one annotated frame per station/position
and save it to data/calibration/.  Inspect the images to tune STATION_ROIS
in config.py.

Usage:
    python3 calibrate_roi.py [--station 1] [--position upper]
    # runs for all stations / both positions by default

The script does NOT start the main FarmCam server.  It uses Picamera2
directly and captures a single still per position.

Requirements:
    - Run while the camera is not locked by another process
    - Set FARMCAM_ESP32_IP if you need motor commands (optional)
"""

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import config

CALIB_DIR = config.CALIB_DIR
CALIB_DIR.mkdir(parents=True, exist_ok=True)


def draw_roi(frame_bgr: np.ndarray, station: int, position: str) -> np.ndarray:
    """Draw the configured ROI box on a copy of the frame."""
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    roi = config.STATION_ROIS.get((station, position), [0, 0, 1, 1])
    x1 = int(roi[0] * w); y1 = int(roi[1] * h)
    x2 = int(roi[2] * w); y2 = int(roi[3] * h)
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 3)
    label = f"Station {station} / {position}"
    cv2.putText(out, label, (x1 + 6, y1 + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    roi_text = f"ROI: [{roi[0]:.2f},{roi[1]:.2f},{roi[2]:.2f},{roi[3]:.2f}]"
    cv2.putText(out, roi_text, (x1 + 6, y1 + 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 0), 2)
    return out


def capture_frame() -> np.ndarray:
    """Capture one frame from Picamera2 and return as BGR numpy array."""
    try:
        from picamera2 import Picamera2
    except ImportError:
        print("[CALIB] picamera2 not available — generating a synthetic frame.")
        return np.zeros((960, 1280, 3), dtype=np.uint8)

    cam = Picamera2()
    cfg = cam.create_still_configuration(
        main={"size": (1280, 960), "format": "BGR888"}
    )
    cam.configure(cfg)
    cam.start()
    time.sleep(2.0)   # let auto-exposure settle
    frame = cam.capture_array()
    cam.stop()
    cam.close()
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description="FarmCam ROI calibration")
    parser.add_argument("--station",  type=int, choices=[1, 2, 3, 4], default=None,
                        help="Capture only this station (default: all)")
    parser.add_argument("--position", choices=["upper", "lower"], default=None,
                        help="Capture only this position (default: both)")
    args = parser.parse_args()

    stations  = [args.station]  if args.station  else config.LETTUCE_SEQUENCE
    positions = [args.position] if args.position else ["upper", "lower"]

    for station in stations:
        for position in positions:
            print(f"[CALIB] Capturing station {station} / {position} ...")
            frame = capture_frame()
            annotated = draw_roi(frame, station, position)
            out_path = CALIB_DIR / f"station{station}_{position}.jpg"
            cv2.imwrite(str(out_path), annotated)
            print(f"[CALIB] Saved: {out_path}")
            print(f"[CALIB] Current ROI: {config.STATION_ROIS.get((station, position))}")
            print("[CALIB] Inspect the image and update STATION_ROIS in config.py if needed.\n")


if __name__ == "__main__":
    main()

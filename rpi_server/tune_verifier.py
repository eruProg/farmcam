"""
tune_verifier.py — Replay rejected detections and tune gate parameters
=======================================================================
Reads all .jpg + .json pairs in REJECTED_DIR (or a custom path),
re-runs the verifier with the current config thresholds, and prints
a gate-by-gate rejection breakdown table.

Usage:
    python3 tune_verifier.py [--input data/rejected] [--show] [--conf 0.35]

Options:
    --input DIR     Directory with rejected *.jpg/*.json pairs  (default: config.REJECTED_DIR)
    --show          Display each image in an OpenCV window for visual inspection
    --conf FLOAT    Override CONFIDENCE_THRESHOLD for this run (default: from config)
    --sigma FLOAT   Override SIGMA_MAX (skin ratio threshold)
    --green FLOAT   Override GREEN_RATIO_MIN
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

# Allow running without the full Pi environment
try:
    import config
    from verifier import (
        DetectionVerifier, _skin_ratio, _green_ratio, _skin_frame_ratio
    )
except ImportError as exc:
    print(f"[tune_verifier] Import error: {exc}")
    sys.exit(1)


def load_pairs(directory: Path):
    """Yield (image_bgr, meta_dict) for each .jpg/.json pair found."""
    for json_path in sorted(directory.glob("*.json")):
        img_path = json_path.with_suffix(".jpg")
        if not img_path.exists():
            continue
        try:
            meta = json.loads(json_path.read_text())
            img  = cv2.imread(str(img_path))
            if img is None:
                continue
            yield img, meta
        except Exception as exc:
            print(f"  [warn] Could not load {json_path.name}: {exc}")


def analyse(directory: Path, show: bool = False) -> None:
    pairs = list(load_pairs(directory))
    if not pairs:
        print(f"[tune_verifier] No rejected pairs found in {directory}")
        return

    gate_counts: Counter = Counter()
    class_counts: Counter = Counter()
    by_class_and_gate: dict = defaultdict(Counter)

    for img, meta in pairs:
        reason = meta.get("reason", "unknown")
        cls    = meta.get("class", "unknown")
        gate_counts[reason]  += 1
        class_counts[cls]    += 1
        by_class_and_gate[cls][reason] += 1

        if show:
            bbox = meta.get("bbox")
            disp = img.copy()
            if bbox:
                x1, y1, x2, y2 = bbox
                cv2.rectangle(disp, (x1, y1), (x2, y2), (0, 0, 255), 2)
                # Compute some stats on the crop
                crop = img[max(0, y1):y2, max(0, x1):x2]
                if crop.size > 0:
                    skin = _skin_ratio(crop)
                    green = _green_ratio(crop)
                    label = f"{cls} | {reason} | skin={skin:.2f} green={green:.2f}"
                    cv2.putText(disp, label, (max(0, x1), max(18, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 100, 255), 1)
            cv2.imshow("tune_verifier – press any key for next, q to quit", disp)
            key = cv2.waitKey(0) & 0xFF
            if key == ord("q"):
                break

    if show:
        cv2.destroyAllWindows()

    # ── Summary table ─────────────────────────────────────────────────────
    total = sum(gate_counts.values())
    print(f"\n{'='*60}")
    print(f"  tune_verifier — {total} rejected detections in {directory}")
    print(f"{'='*60}")
    print(f"\n  By gate reason (top 15):")
    for reason, count in gate_counts.most_common(15):
        pct = 100.0 * count / max(total, 1)
        bar = "█" * int(pct / 2)
        print(f"  {reason:<40s}  {count:4d}  {pct:5.1f}%  {bar}")

    print(f"\n  By class:")
    for cls, count in class_counts.most_common():
        pct = 100.0 * count / max(total, 1)
        print(f"  {cls:<20s}  {count:4d}  {pct:5.1f}%")
        for reason, n in by_class_and_gate[cls].most_common(5):
            print(f"    ↳ {reason:<38s}  {n:4d}")

    print(f"\n  Threshold hints (current config values):")
    print(f"    CONFIDENCE_THRESHOLD  = {config.CONFIDENCE_THRESHOLD}")
    print(f"    PER_CLASS_THRESHOLDS  = {config.PER_CLASS_THRESHOLDS}")
    print(f"    SIGMA_MAX (skin)      = {config.SIGMA_MAX}")
    print(f"    GREEN_RATIO_MIN       = {config.GREEN_RATIO_MIN}")
    print(f"    BRIGHT_V_MIN          = {config.BRIGHT_V_MIN}")
    print(f"    DARK_S_MAX            = {config.DARK_S_MAX}")
    print(f"    PER_CLASS_SIZE        = {config.PER_CLASS_SIZE}")
    print(f"\n  To change: edit config.py, then re-run tune_verifier.py.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay rejected detections and tune gate thresholds."
    )
    parser.add_argument("--input", default=str(config.REJECTED_DIR),
                        help="Directory of rejected .jpg/.json pairs")
    parser.add_argument("--show",  action="store_true",
                        help="Display images in OpenCV window")
    parser.add_argument("--conf",  type=float, default=None,
                        help="Override CONFIDENCE_THRESHOLD")
    parser.add_argument("--sigma", type=float, default=None,
                        help="Override SIGMA_MAX")
    parser.add_argument("--green", type=float, default=None,
                        help="Override GREEN_RATIO_MIN")
    args = parser.parse_args()

    if args.conf  is not None: config.CONFIDENCE_THRESHOLD = args.conf
    if args.sigma is not None: config.SIGMA_MAX            = args.sigma
    if args.green is not None: config.GREEN_RATIO_MIN      = args.green

    analyse(Path(args.input), show=args.show)


if __name__ == "__main__":
    main()

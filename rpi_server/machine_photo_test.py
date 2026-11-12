"""Photo-level fungus test on the machine photos marked in label_health.py
(datasets_fixed/fungus_health_test: images + truth.json healthy/fungus).

Runs the Pi's own steps (crops, checks, minimum, 'at least 2 spots') and
reports, per photo, whether fungus would be reported:
    infected photos found  /  healthy photos falsely flagged

    py machine_photo_test.py --seg "..\\best na pt\\seg\\best_seg_v3_natural.pt"      (Windows, .pt)
    python machine_photo_test.py --seg-har ~/farmcam_compile/best\\ na\\ pt/best_seg.har (WSL, chip)
"""
import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("FARMCAM_PEST_MODEL", "natural")
os.environ.setdefault("FARMCAM_FUNGUS_MODEL", "natural")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cv2                      # noqa: E402
import config                   # noqa: E402
import evaluate                 # noqa: E402
import offline_test as ot       # noqa: E402
from verifier import DetectionVerifier   # noqa: E402

DEFAULT = HERE.parent / "datasets_fixed" / "fungus_health_test"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", type=Path, default=DEFAULT)
    parser.add_argument("--seg", type=Path, help="fungus .pt (Windows)")
    parser.add_argument("--det", type=Path, default=HERE.parent / "datasets" / "pest pt" / "best_detect_natural.pt")
    parser.add_argument("--seg-har", type=Path, help="compiled fungus .har (WSL, emulated chip)")
    parser.add_argument("--har", type=Path, help="compiled pest .har (WSL); default hef_emulator's")
    args = parser.parse_args()
    truth = json.loads((args.folder / "truth.json").read_text(encoding="utf-8-sig"))

    if args.seg_har:
        import hef_emulator as he
        emu = he.EmulatedPi(args.har or he.DEFAULT_HAR, args.seg_har)
        detect = emu.raw_detections
    else:
        from ultralytics import YOLO
        models = {"DET": YOLO(str(args.det)), "SEG": YOLO(str(args.seg))}
        ns = ot.backend_definitions(tiles=True)
        detect = lambda frame: ot.raw_detections(frame, models, ns, "cpu")   # noqa: E731

    threshold = config.PER_CLASS_THRESHOLDS["fungus"]
    config.PER_CLASS_THRESHOLDS["fungus"] = 0.0
    verifier = DetectionVerifier(rejected_dir=HERE / "data" / "eval_unused")
    meta = SimpleNamespace(station=None, position=None)
    found = flagged = infected = healthy = 0
    wrong = []
    for name, answer in sorted(truth.items()):
        frame = ot.to_camera_frame(cv2.imread(str(args.folder / "images" / name)))
        kept = evaluate.frame_rule([d for d, r in verifier.explain(detect(frame), frame, meta)
                                    if d[4] == "fungus" and r is None and d[5] >= threshold], "fungus")
        if answer == "fungus":
            infected += 1
            found += bool(kept)
            if not kept:
                wrong.append((name, "missed"))
        else:
            healthy += 1
            flagged += bool(kept)
            if kept:
                wrong.append((name, f"false fungus ({len(kept)} spots)"))
    print(f"\nMachine photos (minimum {threshold:.2f}, at least {config.FUNGUS_MIN_PER_FRAME} spots per photo):")
    print(f"   infected photos found     {found}/{infected}")
    print(f"   healthy photos flagged    {flagged}/{healthy}")
    for name, why in wrong:
        print(f"   - {name}: {why}")


if __name__ == "__main__":
    main()

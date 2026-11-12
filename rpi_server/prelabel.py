"""Draft labels for unlabelled FarmCam photos, ready to import into Roboflow.

Runs the same pipeline as the Pi (offline_test.py + verifier checks) with a
lower confidence floor, so it suggests more than it would report: deleting a
wrong suggestion in Roboflow is faster than drawing a tiny whitefly box.
YOU STILL REVIEW EVERY IMAGE: fix wrong boxes, add missed ones. Unchecked
draft labels would teach the models their own mistakes.

    py prelabel.py --images "C:\\path\\to\\photos" --skip-labelled

Writes to ..\\datasets\\to_label\\:
  pest\\    images + YOLO boxes (whitefly, worm)  -> upload to whitefly-and-worm
  fungus\\  images + YOLO polygons (fungus)       -> upload to fungus-only
  nothing_found.txt  photos with no suggestion: check them; truly empty ones
                     are your "nothing here" photos (copy to datasets\\negatives)
Images are copied unchanged, names included (auto_default_* photos keep their
swapped colours; prepare_dataset.py fixes them later). Upload each folder by
dragging it into the project's Upload page; Roboflow reads data.yaml + .txt.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                                   # noqa: E402
import offline_test as ot                       # noqa: E402
from verifier import DetectionVerifier          # noqa: E402

DATASETS = HERE.parent / "datasets"
PT_DIR = Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt")
PEST_IDS = {"whiteflies": 0, "worm": 1}          # matches whitefly-and-worm data.yaml
SUGGEST_FLOOR = {"whiteflies": 0.30, "whitefly": 0.30, "worm": 0.35, "fungus": 0.25}


def labelled_keys():
    """Capture timestamps already in the labelled exports (skip duplicates)."""
    keys = set()
    for path in DATASETS.glob("*/*/images/*"):
        match = re.search(r"(\d{8}_\d{6})", path.name)
        if match:
            keys.add(match.group(1))
    return keys


def norm_box(det, w, h):
    x1, y1, x2, y2 = (float(v) for v in det[:4])
    return ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h)


def fungus_polygon(det, w, h):
    poly = det[6] if len(det) > 6 and det[6] else None
    if poly is None or len(poly) < 3:
        x1, y1, x2, y2 = (float(v) for v in det[:4])
        poly = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return [f"{min(max(px / w, 0), 1):.6f} {min(max(py / h, 0), 1):.6f}" for px, py in poly]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DATASETS / "to_label")
    parser.add_argument("--det", type=Path, default=PT_DIR / "best_Pest.pt")
    parser.add_argument("--seg", type=Path, default=PT_DIR / "best_segmentation.pt")
    parser.add_argument("--skip-labelled", action="store_true",
                        help="skip photos whose capture time is already in datasets\\")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    paths = sorted(p for p in args.images.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if args.skip_labelled:
        done = labelled_keys()
        paths = [p for p in paths if not (re.search(r"(\d{8}_\d{6})", p.name) and
                                          re.search(r"(\d{8}_\d{6})", p.name).group(1) in done)]
    paths = paths[:args.limit]
    if not paths:
        raise SystemExit("No new images to label.")

    from ultralytics import YOLO
    models = {"DET": YOLO(str(args.det)), "SEG": YOLO(str(args.seg))}
    ns = ot.backend_definitions(tiles=True)
    verifier = DetectionVerifier(rejected_dir=HERE / "data" / "prelabel_unused")
    meta = SimpleNamespace(station=None, position=None)
    # Suggest more than the Pi would report; the human review removes extras.
    saved = dict(config.PER_CLASS_THRESHOLDS)
    config.PER_CLASS_THRESHOLDS.update(SUGGEST_FLOOR)

    if args.out.exists():
        shutil.rmtree(args.out)
    for sub in ("pest", "fungus"):
        (args.out / sub / "images").mkdir(parents=True)
        (args.out / sub / "labels").mkdir(parents=True)
    (args.out / "pest" / "data.yaml").write_text(yaml.safe_dump({"nc": 2, "names": ["whitefly", "worm"]}))
    (args.out / "fungus" / "data.yaml").write_text(yaml.safe_dump({"nc": 1, "names": ["fungus"]}))

    empty, totals = [], {"whiteflies": 0, "worm": 0, "fungus": 0}
    try:
        for n, path in enumerate(paths, 1):
            image = cv2.imread(str(path))
            if image is None:
                continue
            h0, w0 = image.shape[:2]
            natural = np.ascontiguousarray(image[:, :, ::-1]) if "auto_default" in path.name else image
            frame = ot.to_camera_frame(natural)
            fh, fw = frame.shape[:2]
            kept = [d for d, r in verifier.explain(ot.raw_detections(frame, models, ns, "cpu", "split"),
                                                    frame, meta) if r is None]
            pest = [d for d in kept if d[4] in PEST_IDS]
            fungus = [d for d in kept if d[4] == "fungus"]
            for d in kept:
                totals[d[4]] = totals.get(d[4], 0) + 1
            # Labels are normalised, so they fit the original file at any size.
            for sub, lines in (("pest", [f"{PEST_IDS[d[4]]} " + " ".join(f"{v:.6f}" for v in norm_box(d, fw, fh))
                                         for d in pest]),
                               ("fungus", ["0 " + " ".join(fungus_polygon(d, fw, fh)) for d in fungus])):
                shutil.copy2(path, args.out / sub / "images" / path.name)
                (args.out / sub / "labels" / f"{path.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
            if not kept:
                empty.append(path.name)
            if n % 25 == 0:
                print(f"   {n}/{len(paths)}")
    finally:
        config.PER_CLASS_THRESHOLDS.clear()
        config.PER_CLASS_THRESHOLDS.update(saved)

    (args.out / "nothing_found.txt").write_text("\n".join(empty) + "\n")
    print(f"\n{len(paths)} photos drafted -> {args.out}")
    print(f"suggestions: whitefly {totals['whiteflies']}, worm {totals['worm']}, fungus {totals['fungus']}")
    print(f"{len(empty)} photos with no suggestion listed in nothing_found.txt")


if __name__ == "__main__":
    main()

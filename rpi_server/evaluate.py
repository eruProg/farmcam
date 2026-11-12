"""Measure FarmCam detection accuracy against labelled Roboflow (YOLOv8) data.

Runs the same steps as the Pi (offline_test.py: crops, thresholds, merging,
verifier gates) on each image of a labelled split, then compares the kept
boxes with the labels. Reports per class:
  precision  = kept boxes that are real / all kept boxes
  recall     = labelled objects found / all labelled objects
and, for every missed object, which check rejected the box that would have
found it. A confidence sweep suggests per-class minimums from the data.

    py evaluate.py --data "..\\datasets\\whiteflies and worms" --split test
    py evaluate.py --data ..\\datasets\\fungus --split test --model-colors swapped

Camera photos (auto_default_*) in these exports have red/blue reversed; they
are converted to natural colours first, exactly like the live camera frame.
Not covered: the Pi's 3-of-5-frames temporal check (needs video).
"""
import argparse
import json
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                                                   # noqa: E402
import offline_test as ot                                       # noqa: E402
from verifier import DetectionVerifier                          # noqa: E402

NAME_MAP = {"whitefly": "whiteflies", "whiteflies": "whiteflies", "worm": "worm", "fungus": "fungus"}
SPLITS = ("train", "valid", "test")
SWEEP = [round(t, 2) for t in np.arange(0.25, 0.86, 0.05)]


def read_labels(path, names, width, height):
    """YOLO boxes or polygons -> [(class, x1, y1, x2, y2)] in pixels."""
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        name = NAME_MAP.get(names[int(parts[0])], names[int(parts[0])])
        vals = [float(v) for v in parts[1:]]
        if len(vals) == 4:
            cx, cy, w, h = vals
            box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        else:
            xs, ys = vals[0::2], vals[1::2]
            box = (min(xs), min(ys), max(xs), max(ys))
        out.append((name, box[0] * width, box[1] * height, box[2] * width, box[3] * height))
    return out


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def centre_inside(a, b, pad):
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad


def same_object(name, det, gt):
    """Whiteflies are a few pixels and fungus labels outline single lesions
    while boxes may cover a cluster, so both match more loosely than worms."""
    if name == "worm":
        return iou(det, gt) >= 0.5
    if name == "whiteflies":
        return iou(det, gt) >= 0.3 or centre_inside(det, gt, 4) or centre_inside(gt, det, 4)
    return iou(det, gt) >= 0.3 or centre_inside(det, gt, 0) or centre_inside(gt, det, 0)


def match(dets, gts, name):
    """Greedy by confidence. Returns (tp, fp, matched gt indexes)."""
    used, tp, fp = set(), 0, 0
    for det in sorted(dets, key=lambda d: -d[5]):
        hit = next((i for i, g in enumerate(gts) if i not in used and same_object(name, det[:4], g)), None)
        if hit is None:
            fp += 1
        else:
            used.add(hit)
            tp += 1
    return tp, fp, used


def gate_of(reason):
    return reason.split("_")[0] if reason else "kept"


def pr(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def cache_path(data_dir, split, model_colors, models):
    """Raw model output depends on data, split, colour mode (and for "split" the
    passes chosen by config.PEST_MODEL) and model files."""
    stamp = "_".join(f"{Path(m.ckpt_path).stem}{int(Path(m.ckpt_path).stat().st_mtime)}"
                     for m in models.values())
    mode = f"split-{config.PEST_MODEL}" if model_colors == "split" else model_colors
    if model_colors == "split" and config.FUNGUS_MODEL != "old":
        mode += f"-fungus{config.FUNGUS_MODEL}"
    if config.WHITEFLY_TILE_GRID != 2:
        mode += f"-grid{config.WHITEFLY_TILE_GRID}"
    return HERE / "eval_results" / "cache" / f"{data_dir.name}_{split}_{mode}_{stamp}.pkl"


# At 50 cm with the 16 mm lens the 1280 frame is 6.5 px/mm, so a 1-3 mm whitefly
# is 7-20 px. Labels far above that are internet close-ups the machine never sees.
MACHINE_MAX_PX = {"whiteflies": 40}
# Lesions smaller than the Pi's fungus size gate (frame px^2) are never counted
# by design, so they are not scored as misses either.
MACHINE_MIN_AREA = {"fungus": config.PER_CLASS_SIZE["fungus"][0]}


def split_machine_scale(gts):
    """(labels the camera can produce / the Pi counts, labels it cannot)."""
    real, impossible = [], []
    for g in gts:
        w, h = g[3] - g[1], g[4] - g[2]
        too_big = MACHINE_MAX_PX.get(g[0]) and max(w, h) > MACHINE_MAX_PX[g[0]]
        too_small = MACHINE_MIN_AREA.get(g[0]) and w * h < MACHINE_MIN_AREA[g[0]]
        (impossible if too_big or too_small else real).append(g)
    return real, impossible


def label_coverage(path, names, width, height, name="fungus"):
    """% of the frame covered by this class's labelled outlines (as the Pi
    computes fungus coverage from mask pixels)."""
    if not path.exists():
        return 0.0
    area = 0.0
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5 or NAME_MAP.get(names[int(parts[0])], names[int(parts[0])]) != name:
            continue
        vals = [float(v) for v in parts[1:]]
        if len(vals) == 4:
            area += vals[2] * vals[3]
        else:
            xs, ys = np.array(vals[0::2]), np.array(vals[1::2])
            area += 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))
    return min(100.0, area * 100.0)


def predicted_coverage(kept, width, height):
    """detect_loop's fungus coverage: mask pixels, else the box area."""
    total = 0.0
    for d in kept:
        mask_px = d[7] if len(d) > 7 else 0
        total += mask_px if mask_px > 0 else (d[2] - d[0]) * (d[3] - d[1])
    return min(100.0, total * 100.0 / max(1, width * height))


def fungus_severity(pct):
    """main.py compute_severity_info, fungus half."""
    if pct >= config.SEV_FUNGUS_HIGH_PCT:
        return "high"
    if pct >= getattr(config, "SEV_FUNGUS_MEDIUM_PCT", 5.0):     # main.py uses 5.0
        return "medium"
    return "low" if pct > 0 else "none"


def frame_rule(kept, name):
    """verifier.py's FUNGUS_MIN_PER_FRAME, applied after the minimum confidence
    (the verifier runs here with confidence checks off, for the sweep)."""
    if name == "fungus" and len(kept) < getattr(config, "FUNGUS_MIN_PER_FRAME", 1):
        return []
    return kept


def capture_key(name):
    match = re.search(r"(\d{8}_\d{6})", name)
    return match.group(1) if match else None


def original_for(name, originals):
    """The full-size 4056x3040 photo behind a machine test photo, or None."""
    match = re.search(r"(field_full_\d{8}_\d{6}_\d+)", name)
    path = Path(originals) / f"{match.group(1)}.jpg" if match and originals else None
    return path if path and path.exists() else None


def evaluate(data_dir, split, models, model_colors, device, classes, limit=None, use_cache=True,
             only_from=None, machine_scale=False, detector=None, cache_tag=None,
             wf_cls=None, originals=None):
    """`detector(frame) -> raw detections` replaces the .pt models (e.g. the
    emulated Hailo model in hef_emulator.py); `cache_tag` names its cache.
    wf_cls: the whitefly classifier (.onnx), the Pi's second stage; its zoom
    crops come from the full-size photo in `originals` when there is one."""
    meta = yaml.safe_load((data_dir / "data.yaml").read_text())
    names = meta["names"]
    splits = SPLITS if split == "all" else (split,)
    images = sorted(p for s in splits for p in (data_dir / s / "images").glob("*.jpg"))
    if only_from:
        # Score only photos taken at the same moments as the files in this
        # folder (Roboflow renames uploads but keeps the capture time).
        wanted = {capture_key(p.name) for p in Path(only_from).rglob("*.jpg")} - {None}
        images = [p for p in images if capture_key(p.name) in wanted]
    images = images[:limit]
    ns = ot.backend_definitions(tiles=True)
    classifier = None
    if wf_cls:
        import wf_classifier
        classifier = wf_classifier.load(wf_cls)
        if classifier is None:
            raise SystemExit(f"Could not load the whitefly classifier {wf_cls}")
        # Same as main.py with the classifier installed: weaker candidates reach it.
        for key in ("whitefly", "whiteflies"):
            ns["PER_CLASS_THRESHOLDS"][key] = min(ns["PER_CLASS_THRESHOLDS"][key], config.WF_STAGE1_MIN)
    # Model inference is the slow part; cache it so check settings can be
    # tuned in seconds (the verifier always re-runs on the cached boxes).
    cache_file = (HERE / "eval_results" / "cache" / f"{data_dir.name}_{split}_{cache_tag}.pkl"
                  if detector else cache_path(data_dir, split, model_colors, models))
    if wf_cls:
        cache_file = cache_file.with_name(cache_file.stem + "_wf-stage1.pkl")
    cache = pickle.loads(cache_file.read_bytes()) if use_cache and cache_file.exists() else {}
    fresh = False
    verifier = DetectionVerifier(rejected_dir=HERE / "data" / "eval_unused", classifier=classifier)
    frame_meta = SimpleNamespace(station=None, position=None)
    # Confidence is swept below, so let the verifier judge everything else.
    saved = dict(config.PER_CLASS_THRESHOLDS)
    config.PER_CLASS_THRESHOLDS.update({k: 0.0 for k in saved})
    per_image = []
    try:
        for n, path in enumerate(images, 1):
            image = cv2.imread(str(path))
            if "auto_default" in path.name:              # old capture bug: reversed red/blue
                image = np.ascontiguousarray(image[:, :, ::-1])
            frame = ot.to_camera_frame(image)
            h, w = frame.shape[:2]
            label_path = path.parent.parent / "labels" / f"{path.stem}.txt"
            gts = read_labels(label_path, names, w, h)
            coverage = label_coverage(label_path, names, w, h) if "fungus" in classes else 0.0
            if path.name not in cache:
                cache[path.name] = (detector(frame) if detector else
                                    ot.raw_detections(frame, models, ns, device, model_colors))
                fresh = True
            raw = [list(d) for d in cache[path.name]]
            if classifier is not None:
                original = original_for(path.name, originals)
                full = cv2.imread(str(original)) if original else None
                verifier.zoom = ((lambda boxes, full=full: [wf_classifier.crop_window(b, full) for b in boxes])
                                 if full is not None else None)
            explained = verifier.explain(raw, frame, frame_meta)
            if machine_scale:
                # Neither score nor penalise objects the camera cannot produce.
                gts, impossible = split_machine_scale(gts)
                explained = [(d, r) for d, r in explained
                             if not any(g[0] == d[4] and same_object(d[4], d[:4], g[1:]) for g in impossible)]
            per_image.append(dict(name=path.name, gts=gts, explained=explained, coverage=coverage,
                                  size=(w, h)))
            if n % 25 == 0:
                print(f"   {n}/{len(images)} images")
    finally:
        config.PER_CLASS_THRESHOLDS.clear()
        config.PER_CLASS_THRESHOLDS.update(saved)
        if use_cache and fresh:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(pickle.dumps(cache))

    report = {}
    for name in classes:
        threshold = saved.get(name, config.CONFIDENCE_THRESHOLD)
        if classifier is not None and name == "whiteflies":
            threshold = config.WF_STAGE1_MIN       # the classifier decides above this
        sweep = []
        for t in SWEEP + [threshold]:
            tp = fp = fn = 0
            for item in per_image:
                gts = [g[1:] for g in item["gts"] if g[0] == name]
                kept = frame_rule([d for d, r in item["explained"] if d[4] == name and r is None and d[5] >= t], name)
                a, b, used = match(kept, gts, name)
                tp, fp, fn = tp + a, fp + b, fn + len(gts) - len(used)
            sweep.append((t, tp, fp, fn))
        current = sweep.pop()
        # Why were labelled objects missed at the current threshold?
        missed_by = Counter()
        missed_detail = []      # (image, label w x h px, rejection reason) for gate misses
        for item in per_image:
            gts = [g[1:] for g in item["gts"] if g[0] == name]
            kept = frame_rule([d for d, r in item["explained"]
                               if d[4] == name and r is None and d[5] >= threshold], name)
            _, _, used = match(kept, gts, name)
            for i, gt in enumerate(gts):
                if i in used:
                    continue
                cands = [(d, r) for d, r in item["explained"] if d[4] == name and same_object(name, d[:4], gt)]
                if not cands:
                    missed_by["model found nothing"] += 1
                else:
                    d, r = max(cands, key=lambda c: c[0][5])
                    missed_by["confidence below minimum" if r is None else f"gate: {gate_of(r)}"] += 1
                    missed_detail.append((item["name"], f"{gt[2] - gt[0]:.0f}x{gt[3] - gt[1]:.0f}",
                                          r or f"conf_{d[5]:.2f}"))
        # Per photo: does it say "this class is here" when it is (TP), when it
        # is not (FP), stay quiet on a clean photo (TN), or miss it (FN)?
        photo = Counter()
        for item in per_image:
            truth = any(g[0] == name for g in item["gts"])
            said = bool(frame_rule([d for d, r in item["explained"]
                                    if d[4] == name and r is None and d[5] >= threshold], name))
            photo["TP" if truth and said else "FP" if said else "FN" if truth else "TN"] += 1
        report[name] = dict(threshold=threshold, current=current, sweep=sweep,
                            labelled=current[1] + current[3], missed_by=dict(missed_by),
                            missed_detail=missed_detail, photos=dict(photo))
        if name == "fungus":
            # Would the Pi's fungus severity (none/low/medium/high) be right?
            pairs = []
            for item in per_image:
                kept = frame_rule([d for d, r in item["explained"]
                                   if d[4] == name and r is None and d[5] >= threshold], name)
                pairs.append((fungus_severity(item["coverage"]), fungus_severity(predicted_coverage(kept, *item["size"]))))
            report[name]["severity"] = dict(
                agree=sum(a == b for a, b in pairs), photos=len(pairs),
                table=dict(Counter(f"{a}->{b}" for a, b in pairs if a != b)))
    return report, len(images)


def print_report(report, title):
    print(f"\n=== {title} ===")
    for name, r in report.items():
        t, tp, fp, fn = r["current"]
        p, rc, f = pr(tp, fp, fn)
        print(f"\n{name}: {r['labelled']} labelled objects, minimum confidence {t:.2f}")
        print(f"   precision {p:6.1%}   recall {rc:6.1%}   F1 {f:5.2f}   (found {tp}, false {fp}, missed {fn})")
        if r["missed_by"]:
            print("   missed because: " + ", ".join(f"{k} {v}" for k, v in
                                                  sorted(r["missed_by"].items(), key=lambda kv: -kv[1])))
        ph = r.get("photos") or {}
        tp, fp, tn, fn = (ph.get(k, 0) for k in ("TP", "FP", "TN", "FN"))
        if tp + fp + tn + fn:
            sens = tp / (tp + fn) if tp + fn else 0
            spec = tn / (tn + fp) if tn + fp else 0
            print(f"   per photo: TP {tp}  FP {fp}  TN {tn}  FN {fn}  |  accuracy {(tp + tn) / (tp + fp + tn + fn):.1%}"
                  f"  sensitivity {sens:.1%}  specificity {spec:.1%}")
        sev = r.get("severity")
        if sev:
            print(f"   severity (none/low/medium/high) right on {sev['agree']}/{sev['photos']} photos"
                  + (f"; wrong (labelled->predicted): {sev['table']}" if sev["table"] else ""))
        print("   confidence sweep:  min   precision  recall   F1")
        best = max(r["sweep"], key=lambda s: pr(*s[1:])[2])
        for s in r["sweep"]:
            p, rc, f = pr(*s[1:])
            mark = "  <- best F1" if s is best else ""
            print(f"                     {s[0]:.2f}   {p:7.1%}  {rc:7.1%}  {f:5.2f}{mark}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="Roboflow YOLOv8 export folder (has data.yaml)")
    parser.add_argument("--split", default="test", choices=["train", "valid", "test", "all"])
    parser.add_argument("--only-from", type=Path,
                        help="score only photos also found in this folder (matched by capture time), "
                             "e.g. ..\\datasets\\to_label\\train_pest_clean")
    parser.add_argument("--det", type=Path, default=Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt\best_Pest.pt"))
    parser.add_argument("--seg", type=Path, default=Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt\best_segmentation.pt"))
    parser.add_argument("--model-colors", nargs="+", default=["split"], choices=["split", "swapped", "natural"],
                        help="one or more colour modes to compare")
    parser.add_argument("--classes", nargs="+", help="classes to score (default: the dataset's own)")
    parser.add_argument("--limit", type=int, help="only the first N images (quick check)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--no-cache", action="store_true", help="re-run the models even if cached")
    parser.add_argument("--json", type=Path, help="also save the full report here")
    parser.add_argument("--machine-scale", action="store_true",
                        help="ignore labels too big to occur at the machine's 50 cm "
                             f"(whiteflies over {MACHINE_MAX_PX['whiteflies']} px: internet close-ups)")
    parser.add_argument("--wf-cls", type=Path, help="whitefly classifier .onnx (the Pi's second stage)")
    parser.add_argument("--originals", type=Path,
                        default=HERE.parent / "whitefly datasets and negatives" / "FarmCam captures",
                        help="full-size machine photos, for the classifier's zoom crops")
    args = parser.parse_args()
    # Passes and minimums follow config.PEST_MODEL, like the Pi.
    print(f"Pest model setting: {config.PEST_MODEL} (FARMCAM_PEST_MODEL; 'natural' for the retrained model)")

    names = yaml.safe_load((args.data / "data.yaml").read_text())["names"]
    classes = args.classes or [NAME_MAP.get(n, n) for n in names]
    # A fungus-only model cannot be scored on the fungus export's stray whitefly labels.
    if "fungus" in classes and "worm" not in classes:
        classes = ["fungus"]
    from ultralytics import YOLO
    models = {"DET": YOLO(str(args.det)), "SEG": YOLO(str(args.seg))}
    full = {}
    for mode in args.model_colors:
        print(f"\nEvaluating {args.data.name}/{args.split} with model colours '{mode}' ...")
        report, count = evaluate(args.data, args.split, models, mode, args.device, classes, args.limit,
                                 use_cache=not args.no_cache,
                                 only_from=args.only_from, machine_scale=args.machine_scale,
                                 wf_cls=args.wf_cls, originals=args.originals)
        scale = " / machine scale only" if args.machine_scale else ""
        print_report(report, f"{args.data.name} / {args.split} ({count} images) / model colours: {mode}{scale}")
        full[mode] = report
    if args.json:
        args.json.write_text(json.dumps(full, indent=2))
        print(f"\nSaved {args.json}")


if __name__ == "__main__":
    main()

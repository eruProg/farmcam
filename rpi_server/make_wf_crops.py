"""Build the training crops for the whitefly classifier (wf_classifier.py), the
second deep-learning stage of whitefly detection.

  whitefly      every labelled whitefly. Machine photos are cut from the
                full-size 4056x3040 originals, exactly like the Pi cuts its
                zoom crops; other photos from their own pixels, and only
                whiteflies at least MIN_SOURCE_PX wide there (smaller ones are
                a few blurry pixels, nothing like the Pi's sharp crops)
  not_whitefly  what the YOLO model (stage 1) wrongly calls a whitefly: every
                candidate at >= --conf that is not a labelled whitefly (glare,
                water drops, veins, dirt). The hardest ones first, at most
                --neg-ratio per whitefly in each split.

Machine photos marked Null still hold unlabelled whiteflies (the Oct 27
session), and stage 1 finds them. So an unlabelled machine candidate scoring
>= AMBIGUOUS is left out, unless --reviewed (review_candidates.py) says what
it is: confirmed whiteflies become positives, the rest negatives.

Splits follow the detection datasets (prepare_dataset.py), so no photo of the
pest test sets ever trains the classifier: machine photos use machine_tiles'
burst split, the other photos keep their Roboflow split.

    py make_wf_crops.py --export "..\\datasets\\machine pest v5" --det ..\\v3\\best_detect_v3_natural.pt
    -> ..\\datasets_fixed\\wf_cls\\{train,val,test}\\{not_whitefly,whitefly}\\*.jpg and ..\\wf_cls.zip
"""
import argparse
import csv
import pickle
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml

import offline_test as ot
from evaluate import MACHINE_MAX_PX, read_labels, same_object
from machine_tiles import split_photos
from wf_classifier import CLASSES, crop_window

HERE = Path(__file__).resolve().parent
THESIS = HERE.parent
SPLIT_NAMES = {"train": "train", "valid": "val", "test": "test"}
MAX_WF_PX = MACHINE_MAX_PX["whiteflies"]
MIN_SOURCE_PX = 12        # a whitefly narrower than this in its photo is too blurry to learn from
AMBIGUOUS = 0.30          # unlabelled machine candidates from here up may be real whiteflies


def photo_sources(path, originals):
    """(1280-wide detection frame, image the crops are cut from, crop scale)."""
    if path.name.startswith("field_"):
        original = originals / (path.name.split("_jpg.rf.")[0] + ".jpg")
        full = cv2.imread(str(original)) if original.exists() else None
        if full is not None:
            return cv2.resize(full, (1280, 960), interpolation=cv2.INTER_AREA), full, None
    image = cv2.imread(str(path))
    if "auto_default" in path.name:                # old capture bug: red/blue reversed
        image = np.ascontiguousarray(image[:, :, ::-1])
    frame = ot.to_camera_frame(image)
    scale = image.shape[1] / frame.shape[1]        # cut from the photo's own pixels, not the enlarged frame
    return frame, image, (scale, scale)


def photo_stem(name):
    return name.split("_jpg.rf.")[0].split(".rf.")[0]


def load_reviewed(path):
    """review_candidates.py answers: {photo stem: [((x1, y1, x2, y2), True/False)]}."""
    answers = defaultdict(list)
    if path and Path(path).exists():
        for row in csv.DictReader(Path(path).open()):
            box = tuple(float(row[k]) for k in ("x1", "y1", "x2", "y2"))
            answers[row["photo"]].append((box, row["answer"].strip().lower() in ("yes", "whitefly", "y", "1", "true")))
    return answers


def review_answer(reviewed, stem, box):
    """The reviewed answer for this candidate (same spot within 3 px), or None."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for (x1, y1, x2, y2), answer in reviewed.get(stem, ()):
        if abs((x1 + x2) / 2 - cx) <= 3 and abs((y1 + y2) / 2 - cy) <= 3:
            return answer
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=Path, default=THESIS / "datasets" / "machine pest v5")
    parser.add_argument("--originals", type=Path,
                        default=THESIS / "whitefly datasets and negatives" / "FarmCam captures")
    parser.add_argument("--det", type=Path, default=THESIS / "v3" / "best_detect_v3_natural.pt")
    parser.add_argument("--out", type=Path, default=THESIS / "datasets_fixed" / "wf_cls")
    parser.add_argument("--conf", type=float, default=0.15, help="stage-1 candidates down to this score")
    parser.add_argument("--neg-ratio", type=float, default=5.0)
    parser.add_argument("--reviewed", type=Path, default=THESIS / "reviewed_whiteflies.csv",
                        help="answers from review_candidates.py (photo,x1,y1,x2,y2,answer)")
    parser.add_argument("--machine-only", action="store_true",
                        help="only machine photos: the older close-ups taught the classifier that long "
                             "white shapes (glare streaks) are whiteflies")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    reviewed = load_reviewed(args.reviewed)
    if reviewed:
        answers = [a for items in reviewed.values() for _, a in items]
        print(f"Using {len(answers)} reviewed candidates from {args.reviewed.name} ({sum(answers)} whiteflies)")

    from ultralytics import YOLO
    names = yaml.safe_load((args.export / "data.yaml").read_text())["names"]
    photos = sorted(p for s in SPLIT_NAMES for p in (args.export / s / "images").glob("*.jpg"))
    machine_plan = split_photos([p.name for p in photos if p.name.startswith("field_")])
    ns = ot.backend_definitions(tiles=True)
    ns["PER_CLASS_THRESHOLDS"] = dict(ns["PER_CLASS_THRESHOLDS"], whitefly=args.conf, whiteflies=args.conf)
    models = {"DET": YOLO(str(args.det))}
    # Stage-1 candidates per photo, so re-running after a review takes seconds.
    cache_file = HERE / "eval_results" / "cache" / f"wf_candidates_{args.det.stem}_{args.conf:.2f}.pkl"
    cache = pickle.loads(cache_file.read_bytes()) if cache_file.exists() else {}

    positives, negatives = defaultdict(list), defaultdict(list)       # split -> [(crop, name, conf)]
    rows, stats = [], Counter()
    for n, path in enumerate(photos, 1):
        machine = path.name.startswith("field_")
        if args.machine_only and not machine:
            continue
        split = SPLIT_NAMES[machine_plan[path.name] if machine else path.parent.parent.name]
        label = path.parent.parent / "labels" / f"{path.stem}.txt"
        frame_probe = cv2.imread(str(path))
        h, w = frame_probe.shape[:2]
        if not machine and not any(line.split()[:1] and names[int(line.split()[0])].startswith("whitef")
                                   for line in (label.read_text().splitlines() if label.exists() else [])):
            continue                              # worm-only photos: no whitefly examples either way
        frame, source, scale = photo_sources(path, args.originals)
        fh, fw = frame.shape[:2]
        flies = [g for g in read_labels(label, names, fw, fh) if g[0] == "whiteflies"]
        stem = photo_stem(path.name)
        source_px = scale[0] if scale else source.shape[1] / 1280

        def crops_of(box):
            """The sharp crop, plus for machine photos the 1280x960 one the Pi
            falls back to when the camera zoom times out."""
            out = [(crop_window(box, source, scale), "")]
            if machine and scale is None:
                out.append((crop_window(box, frame), "_lo"))
            return out

        for i, g in enumerate(flies):
            if not machine and max(g[3] - g[1], g[4] - g[2]) * source_px < MIN_SOURCE_PX:
                stats["blurry tiny whiteflies skipped"] += 1
                continue
            for crop, suffix in crops_of(g[1:]):
                positives[split].append((crop, f"{stem}_p{i}{suffix}", 1.0))
            rows.append((split, "whitefly", path.name, *(round(v, 1) for v in g[1:]), ""))
        if path.name not in cache:
            cache[path.name] = ot.raw_detections(frame, models, ns, args.device, "natural")
        for i, det in enumerate(cache[path.name]):
            if det[4] != "whiteflies" or max(det[2] - det[0], det[3] - det[1]) > MAX_WF_PX:
                continue
            if any(same_object("whiteflies", det[:4], g[1:]) for g in flies):
                continue                          # a real whitefly (labelled), not an example of a mistake
            conf = float(det[5])
            answer = review_answer(reviewed, stem, [float(v) for v in det[:4]]) if machine else None
            box = tuple(round(float(v), 1) for v in det[:4])
            if answer is True:                     # an unlabelled whitefly the review confirmed
                for crop, suffix in crops_of(det[:4]):
                    positives[split].append((crop, f"{stem}_r{i}{suffix}", 1.0))
                rows.append((split, "whitefly", path.name, *box, round(conf, 3)))
                stats["reviewed whiteflies added"] += 1
                continue
            if machine and answer is None and conf >= AMBIGUOUS:
                stats["unreviewed machine candidates left out"] += 1
                rows.append((split, "unreviewed", path.name, *box, round(conf, 3)))
                continue
            for crop, suffix in crops_of(det[:4]):
                negatives[split].append((crop, f"{stem}_n{i}{suffix}", conf))
            rows.append((split, "not_whitefly", path.name, *box, round(conf, 3)))
        stats[f"{'machine' if machine else 'other'} photos"] += 1
        if n % 50 == 0:
            print(f"   {n}/{len(photos)} photos")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(pickle.dumps(cache))
    if args.out.exists():
        shutil.rmtree(args.out)
    for split in SPLIT_NAMES.values():
        keep = sorted(negatives[split], key=lambda item: -item[2])           # hardest first
        keep = keep[:int(args.neg_ratio * max(1, len(positives[split])))]
        for cls, items in (("whitefly", positives[split]), ("not_whitefly", keep)):
            folder = args.out / split / cls
            folder.mkdir(parents=True, exist_ok=True)
            for crop, name, _ in items:
                cv2.imwrite(str(folder / f"{name}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            stats[f"{split} {cls}"] = len(items)
        stats[f"{split} not_whitefly available"] = len(negatives[split])
    assert sorted(p.name for p in (args.out / "train").iterdir()) == sorted(CLASSES)
    with (args.out / "crops.csv").open("w", newline="") as f:
        csv.writer(f).writerows([("split", "class", "photo", "x1", "y1", "x2", "y2", "stage1_conf")] + rows)
    archive = shutil.make_archive(str(args.out.parent.parent / args.out.name), "zip", root_dir=args.out.parent,
                                  base_dir=args.out.name)
    print("Whitefly classifier crops: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    print(f"Upload this to Google Drive for Colab: {archive}")


if __name__ == "__main__":
    main()

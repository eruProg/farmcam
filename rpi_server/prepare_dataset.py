"""Build colour-fixed training datasets from the Roboflow YOLOv8 exports.

  - Camera photos (auto_default_*) were saved with red and blue reversed by
    an old capture bug; they are flipped back to natural colours. Labels stay
    as they are (swapping colours does not move any box).
  - The fungus export also contains 'whitefly' labels; the fungus model is
    fungus-only, so those lines are dropped.
  - Optional: images in --negatives (casing, pot, glare, droplets, wall,
    hands; nothing to detect) are added to train with empty label files, so
    the models learn what is NOT a pest.

    py prepare_dataset.py --negatives C:\\path\\to\\negatives
    -> ..\\datasets_fixed\\pest, ..\\datasets_fixed\\fungus and datasets_fixed.zip

    py prepare_dataset.py --only fungus --fungus "..\\datasets\\fungus (new dataset)" --resplit-fungus
    -> rebuilds only the fungus set, with an honest train/valid/test split
       (see resplit) and a label sanity report; the pest set is left as it is.

Nothing in the original exports is changed.
"""
import argparse
import csv
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
DATASETS = HERE.parent / "datasets"
SPLITS = ("train", "valid", "test")
MACHINE_PREFIX = "field_"      # photos taken by the machine (field_full_*.jpg)


def box_to_polygon(parts):
    """YOLO box 'c cx cy w h' -> 4-corner polygon 'c x1 y1 x2 y1 x2 y2 x1 y2'."""
    cx, cy, w, h = (float(v) for v in parts[1:5])
    x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    return [parts[0]] + [f"{v:.6f}" for v in (x1, y1, x2, y1, x2, y2, x1, y2)]


def polygon_to_box(parts):
    """YOLO polygon 'c x1 y1 x2 y2 ...' -> box 'c cx cy w h'."""
    vals = [float(v) for v in parts[1:]]
    xs, ys = vals[0::2], vals[1::2]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    return [parts[0]] + [f"{v:.6f}" for v in ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)]


def polygon_area_px(parts, width, height):
    vals = [float(v) for v in parts[1:]]
    xs, ys = np.array(vals[0::2]) * width, np.array(vals[1::2]) * height
    return 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))


def _bbox(parts):
    vals = [float(v) for v in parts[1:]]
    return min(vals[0::2]), min(vals[1::2]), max(vals[0::2]), max(vals[1::2])


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / union if union > 0 else 0.0


def fix_split(src, dst, keep_classes=None, remap=None, polygons_only=False, boxes_only=False,
              min_area_px=0, dedupe=False, stats=None, skip_prefix=None):
    """Copy one split; un-swap camera photos; filter label lines.

    A label file that mixes boxes and polygons is rejected as "corrupt" by
    Ultralytics (the whole photo is skipped), so each set is made uniform:
    polygons_only: segmentation set, boxes become rectangle polygons.
    boxes_only: detection set, polygons become their bounding boxes.
    min_area_px: drop outlines smaller than this (photo pixels); the fungus
    export has hundreds of 1-8 px outlines on plain leaf (stray clicks), far
    below what the Pi counts. dedupe: drop near-identical repeated outlines.
    skip_prefix: leave these photos out (machine photos, cut by machine_tiles)."""
    (dst / "images").mkdir(parents=True, exist_ok=True)
    (dst / "labels").mkdir(parents=True, exist_ok=True)
    flipped = dropped = converted = 0
    stats = stats if stats is not None else Counter()
    for image in sorted((src / "images").iterdir()):
        if skip_prefix and image.name.startswith(skip_prefix):
            stats["machine photos left to machine_tiles"] += 1
            continue
        # Renamed so colour_check/evaluate never treat a fixed photo as swapped.
        stem = image.stem.replace("auto_default", "camfixed")
        target = dst / "images" / f"{stem}{image.suffix}"
        pixels = cv2.imread(str(image))
        if pixels is None:
            print(f"   skipped unreadable image {image.name}")
            continue
        if "auto_default" in image.name:
            cv2.imwrite(str(target), pixels[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 95])
            flipped += 1
        else:
            shutil.copy2(image, target)
        height, width = pixels.shape[:2]
        label = src / "labels" / f"{image.stem}.txt"
        lines = label.read_text().splitlines() if label.exists() else []
        kept, kept_boxes = [], []
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            cls = int(parts[0])
            if keep_classes is not None and cls not in keep_classes:
                dropped += 1
                continue
            if polygons_only and len(parts) == 5:
                parts = box_to_polygon(parts)
                converted += 1
            elif boxes_only and len(parts) > 5:
                parts = polygon_to_box(parts)
                converted += 1
            if min_area_px and len(parts) > 5 and polygon_area_px(parts, width, height) < min_area_px:
                stats["tiny outlines dropped"] += 1
                continue
            if dedupe and len(parts) > 5:
                box = _bbox(parts)
                if any(_iou(box, other) > 0.9 for other in kept_boxes):
                    stats["duplicate outlines dropped"] += 1
                    continue
                kept_boxes.append(box)
            kept.append(" ".join([str(remap.get(cls, cls) if remap else cls)] + parts[1:]))
        if min_area_px and not kept and any(l.split()[:1] == [str(c)] for l in lines
                                            for c in (keep_classes or {0})):
            # Every outline was tiny: the photo has (small) fungus, so it must
            # not become a "healthy" example. Leave it out entirely.
            target.unlink()
            stats["photos left out (only tiny outlines)"] += 1
            continue
        (dst / "labels" / f"{stem}.txt").write_text("\n".join(kept) + ("\n" if kept else ""))
    return flipped, dropped, converted


def add_negatives(folder, dst, prefix):
    count = 0
    for image in sorted(folder.iterdir()):
        if image.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        name = f"{prefix}_{image.stem}.jpg"
        img = cv2.imread(str(image))
        if img is None:
            continue
        if "auto_default" in image.name:
            img = img[:, :, ::-1]
        cv2.imwrite(str(dst / "images" / name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        (dst / "labels" / f"{prefix}_{image.stem}.txt").write_text("")
        count += 1
    return count


def add_extra(folder, dst):
    """Ready-made labelled images (e.g. synthetic whiteflies) -> train only."""
    count = 0
    for image in sorted((folder / "images").glob("*.jpg")):
        label = folder / "labels" / f"{image.stem}.txt"
        if label.exists():
            shutil.copy2(image, dst / "images" / image.name)
            shutil.copy2(label, dst / "labels" / label.name)
            count += 1
    return count


def build(src, dst, names, keep_classes=None, remap=None, negatives=None, polygons_only=False,
          boxes_only=False, extra=None, min_area_px=0, dedupe=False, skip_prefix=None):
    if dst.exists():
        shutil.rmtree(dst)
    total_flipped = total_dropped = total_converted = 0
    stats = Counter()
    for split in SPLITS:
        if (src / split / "images").exists():
            flipped, dropped, converted = fix_split(src / split, dst / split, keep_classes, remap,
                                                    polygons_only, boxes_only, min_area_px, dedupe, stats,
                                                    skip_prefix)
            total_flipped += flipped
            total_dropped += dropped
            total_converted += converted
    negs = add_negatives(negatives, dst / "train", "negative") if negatives else 0
    extras = add_extra(extra, dst / "train") if extra else 0
    if extras:
        print(f"{dst.name}: added {extras} extra labelled images from {extra.name} (train only)")
    # No 'path:' key: Ultralytics then resolves these relative to this file.
    (dst / "data.yaml").write_text(yaml.safe_dump(
        {"train": "train/images", "val": "valid/images", "test": "test/images",
         "nc": len(names), "names": names}, sort_keys=False))
    counts = {s: len(list((dst / s / "images").glob("*"))) for s in SPLITS if (dst / s).exists()}
    print(f"{dst.name}: {counts}; colour-fixed {total_flipped} camera photos; "
          f"dropped {total_dropped} label lines; label shapes unified {total_converted}; "
          f"added {negs} negative images" + (f"; {dict(stats)}" if stats else ""))


TIME_RE = re.compile(r"(20\d{6})_?(\d{6})")
SPLIT_SHARE = {"train": 0.75, "valid": 0.125, "test": 0.125}


def capture_time(name):
    match = TIME_RE.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def capture_groups(names, gap_s=60):
    """Photos taken in one burst (< gap_s apart) form one group: near-identical
    frames must land in the same split, or the test set is just the training
    set again. Photos without a capture time are their own group."""
    timed = sorted((capture_time(n), n) for n in names if capture_time(n))
    groups, current, last = [], [], None
    for t, name in timed:
        if last is not None and (t - last).total_seconds() > gap_s:
            groups.append(current)
            current = []
        current.append(name)
        last = t
    if current:
        groups.append(current)
    return groups + [[n] for n in names if not capture_time(n)]


def resplit(dst, worm_times=frozenset(), seed=7):
    """Re-deal all photos of a built set into train/valid/test by capture group,
    stratified so valid and test each get machine photos, worm photos and
    clean photos (needed to measure false alarms). Deterministic (seed)."""
    items = {}
    for split in SPLITS:
        for image in (dst / split / "images").glob("*"):
            items[image.name] = (split, image, dst / split / "labels" / f"{image.stem}.txt")

    def kind(group):
        labelled = any(items[n][2].exists() and items[n][2].read_text().strip() for n in group)
        if any(capture_time(n) in worm_times for n in group):
            return "worm photos"
        if not labelled:
            return "clean photos"
        return "machine photos" if any("camfixed" in n for n in group) else "other photos"

    by_kind = defaultdict(list)
    for group in capture_groups(sorted(items)):
        by_kind[kind(group)].append(group)
    rng = random.Random(seed)
    plan, summary = {}, defaultdict(Counter)
    for k, groups in sorted(by_kind.items()):
        total = sum(len(g) for g in groups)
        best = None
        # Groups are uneven (one burst can be 40 photos), so try many orders
        # and keep the deal closest to the shares, with valid and test never
        # empty when there are enough groups.
        for _ in range(500):
            order = groups[:]
            rng.shuffle(order)
            filled, deal = Counter(), []
            for group in order:
                # The split furthest below its share of this kind gets the group.
                split = max(SPLIT_SHARE, key=lambda s: SPLIT_SHARE[s] * total - filled[s])
                filled[split] += len(group)
                deal.append((group, split))
            error = sum(abs(filled[s] - SPLIT_SHARE[s] * total) for s in SPLIT_SHARE)
            if len(groups) >= 3:
                error += 1000 * sum(filled[s] == 0 for s in ("valid", "test"))
            if best is None or error < best[0]:
                best = (error, filled, deal)
        _, filled, deal = best
        for group, split in deal:
            for name in group:
                plan[name] = split
        summary[k] = filled
    staging = dst / "_resplit"
    for name, (split, image, label) in items.items():
        for sub, path in (("images", image), ("labels", label)):
            if path.exists():
                target = staging / plan[name] / sub / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
    for split in SPLITS:
        shutil.rmtree(dst / split, ignore_errors=True)
        if (staging / split).exists():
            shutil.move(str(staging / split), str(dst / split))
    shutil.rmtree(staging, ignore_errors=True)
    print(f"{dst.name}: re-split by capture burst ({len(items)} photos):")
    for k, filled in sorted(summary.items()):
        print(f"   {k:15s} " + "  ".join(f"{s} {filled[s]:3d}" for s in SPLITS))


def label_report(dst, report_csv):
    """Suspicious segmentation labels, for fixing in Roboflow (not auto-fixed)."""
    rows = []
    for split in SPLITS:
        for label in sorted((dst / split / "labels").glob("*.txt")):
            image = next((dst / split / "images").glob(f"{label.stem}.*"), None)
            img = cv2.imread(str(image)) if image else None
            if img is None:
                continue
            h, w = img.shape[:2]
            boxes = []
            for i, line in enumerate(label.read_text().splitlines()):
                vals = [float(v) for v in line.split()[1:]]
                if len(vals) < 6:
                    continue
                xs, ys = np.array(vals[0::2]) * w, np.array(vals[1::2]) * h
                area = 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))
                box = (xs.min(), ys.min(), xs.max(), ys.max())
                if area < 9:
                    rows.append((split, label.stem, i + 1, "tiny (< 3x3 px)", f"{area:.1f} px2"))
                elif area > 0.4 * w * h:
                    rows.append((split, label.stem, i + 1, "huge (> 40% of photo)", f"{area / (w * h):.0%}"))
                if len(xs) == 4 and len(set(np.round(xs, 1))) == 2 and len(set(np.round(ys, 1))) == 2:
                    rows.append((split, label.stem, i + 1, "box, not an outline", ""))
                for j, other in boxes:
                    ix = max(0, min(box[2], other[2]) - max(box[0], other[0]))
                    iy = max(0, min(box[3], other[3]) - max(box[1], other[1]))
                    union = ((box[2] - box[0]) * (box[3] - box[1]) + (other[2] - other[0]) * (other[3] - other[1])
                             - ix * iy)
                    if union > 0 and ix * iy / union > 0.9:
                        rows.append((split, label.stem, i + 1, f"duplicate of line {j}", ""))
                boxes.append((i + 1, box))
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", newline="") as f:
        csv.writer(f).writerows([("split", "photo", "label line", "problem", "detail")] + rows)
    counts = Counter(r[3].split(" of ")[0] for r in rows)
    print(f"{dst.name}: label check: {dict(counts) or 'nothing suspicious'} -> {report_csv}")


def add_machine_health(health_json, fungus_dst, test_dst, train_share=2 / 3, seed=7):
    """Photos marked in label_health.py: about `train_share` of the healthy
    ones (whole capture bursts) become healthy (empty-label) training photos;
    the rest of the healthy ones plus every fungus one form a photo-level
    machine test (test_dst/images + truth.json). 'unsure' is left out."""
    health_json = Path(health_json)
    answers = json.loads(health_json.read_text(encoding="utf-8-sig"))
    folder = health_json.parent / "images"
    healthy = sorted(n for n, a in answers.items() if a == "healthy")
    fungus = sorted(n for n, a in answers.items() if a == "fungus")
    groups = capture_groups(healthy)
    random.Random(seed).shuffle(groups)
    train, held = [], []
    for group in groups:
        (train if len(train) < train_share * len(healthy) else held).extend(group)

    def write(name, dst):
        image = cv2.imread(str(folder / name))
        if "auto_default" in name:                # old capture: red/blue reversed
            image = image[:, :, ::-1]
        stem = "camfixed_field_" + re.sub(r"[^A-Za-z0-9_]+", "_", Path(name).stem.replace("auto_default", ""))
        cv2.imwrite(str(dst / "images" / f"{stem}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return f"{stem}.jpg"

    for name in train:
        out = write(name, fungus_dst / "train")
        (fungus_dst / "train" / "labels" / f"{Path(out).stem}.txt").write_text("")
    if test_dst.exists():
        shutil.rmtree(test_dst)
    (test_dst / "images").mkdir(parents=True)
    truth = {write(name, test_dst): answers[name] for name in held + fungus}
    (test_dst / "truth.json").write_text(json.dumps(truth, indent=1, sort_keys=True))
    print(f"machine photos: {len(train)} healthy added to fungus/train; machine test = "
          f"{len(held)} healthy + {len(fungus)} fungus -> {test_dst}")


def worm_capture_times(pest_dir):
    """Capture times of photos the pest labels say contain a worm (class 1)."""
    times = set()
    for label in Path(pest_dir).glob("*/labels/*.txt"):
        if any(line.split()[:1] == ["1"] for line in label.read_text().splitlines()):
            t = capture_time(label.name)
            if t:
                times.add(t)
    return frozenset(times)


def reviewed_whiteflies(path, class_id):
    """{photo stem: [(class_id, box corners in 1280x960 px, True)]} for every
    candidate answered "yes" in review_candidates.py (none without the file)."""
    extra = defaultdict(list)
    if path and Path(path).exists():
        for row in csv.DictReader(Path(path).open()):
            if row["answer"].strip().lower() not in ("yes", "whitefly", "y", "1", "true"):
                continue
            x1, y1, x2, y2 = (float(row[k]) for k in ("x1", "y1", "x2", "y2"))
            extra[row["photo"]].append((class_id, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True))
        print(f"   {sum(len(v) for v in extra.values())} reviewed whiteflies added from {Path(path).name}")
    return extra


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pest", type=Path, default=DATASETS / "whitefly and worm (new, no augment)")
    parser.add_argument("--fungus", type=Path, default=DATASETS / "fungus")
    parser.add_argument("--out", type=Path, default=HERE.parent / "datasets_fixed")
    parser.add_argument("--only", choices=["all", "pest", "fungus"], default="all",
                        help="rebuild only this set; the other one is left untouched")
    parser.add_argument("--resplit-fungus", action="store_true",
                        help="re-deal fungus photos into train/valid/test by capture burst")
    parser.add_argument("--healthy-from", type=Path,
                        help="health.json from label_health.py: healthy machine photos for fungus "
                             "training + a photo-level machine test (..\\datasets_fixed\\fungus_health_test)")
    parser.add_argument("--machine-pest", type=Path,
                        help="Roboflow export (original size) of the machine photos with whitefly/worm boxes")
    parser.add_argument("--machine-fungus", type=Path,
                        help="Roboflow export (original size) of the machine photos with fungus/dirt outlines")
    parser.add_argument("--machine-originals", type=Path,
                        default=HERE.parent / "whitefly datasets and negatives" / "FarmCam captures",
                        help="the full-size field_full_*.jpg photos; used instead of the export's copies "
                             "when Roboflow shrank them (e.g. 'Resize to 640')")
    parser.add_argument("--reviewed", type=Path, default=HERE.parent / "reviewed_whiteflies.csv",
                        help="review_candidates.py answers: whiteflies confirmed on machine photos "
                             "marked Null are added to the machine pest set")
    parser.add_argument("--min-fungus-px", type=float, default=9.0,
                        help="drop fungus outlines smaller than this many photo pixels (3x3 = 9)")
    parser.add_argument("--negatives", type=Path, help="folder of images with nothing to detect")
    parser.add_argument("--extra-pest", type=Path, help="extra labelled pest images for train only "
                                                         "(e.g. ..\\datasets\\synthetic_whiteflies)")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    if args.only in ("all", "pest"):
        pest_names = yaml.safe_load((args.pest / "data.yaml").read_text())["names"]
        if pest_names != ["whitefly", "worm"]:
            raise SystemExit(f"Unexpected pest classes {pest_names}; the Pi expects ['whitefly', 'worm']")
        build(args.pest, args.out / "pest", pest_names, negatives=args.negatives, extra=args.extra_pest,
              boxes_only=True, skip_prefix=MACHINE_PREFIX if args.machine_pest else None)
        if args.machine_pest:
            from machine_tiles import add_machine_set
            add_machine_set(args.machine_pest, args.out / "pest", args.out / "pest_machine_test",
                            pest_names, as_boxes=True, originals=args.machine_originals,
                            extra=reviewed_whiteflies(args.reviewed, pest_names.index("whitefly")))
    if args.only in ("all", "fungus"):
        if args.healthy_from and args.machine_fungus:
            raise SystemExit("Use --healthy-from or --machine-fungus, not both: the healthy photos are "
                             "the same machine photos, so they would land in training and test at once")
        fungus_names = yaml.safe_load((args.fungus / "data.yaml").read_text())["names"]
        # With the machine set the model has two classes; keep any dirt the
        # base export has too (a single export can hold both kinds of photo).
        wanted = ["fungus", "dirt"] if args.machine_fungus else ["fungus"]
        remap = {fungus_names.index(n): i for i, n in enumerate(wanted) if n in fungus_names}
        build(args.fungus, args.out / "fungus", wanted, keep_classes=set(remap),
              remap=remap, negatives=args.negatives, polygons_only=True,
              min_area_px=args.min_fungus_px, dedupe=True,
              skip_prefix=MACHINE_PREFIX if args.machine_fungus else None)
        if args.resplit_fungus:
            resplit(args.out / "fungus", worm_capture_times(args.out / "pest"))
        label_report(args.out / "fungus", HERE / "eval_results" / "fungus_label_check.csv")
        if args.healthy_from:
            # After the re-split, so these never move into valid/test.
            add_machine_health(args.healthy_from, args.out / "fungus", args.out / "fungus_health_test")
        if args.machine_fungus:
            # Second class "dirt" (fly droppings, soil, dirt in the cup): learned
            # so the model stops calling it fungus; the Pi never counts it.
            from machine_tiles import add_machine_set
            classes = ["fungus", "dirt"]
            add_machine_set(args.machine_fungus, args.out / "fungus", args.out / "fungus_machine_test",
                            classes, as_boxes=False, originals=args.machine_originals)
            (args.out / "fungus" / "data.yaml").write_text(yaml.safe_dump(
                {"train": "train/images", "val": "valid/images", "test": "test/images",
                 "nc": len(classes), "names": classes}, sort_keys=False))
    if not args.no_zip:
        archive = shutil.make_archive(str(args.out), "zip", root_dir=args.out)
        print(f"Upload this to Google Drive for Colab: {archive}")


if __name__ == "__main__":
    main()

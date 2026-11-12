"""Turn full-resolution machine photos (4056x3040, labelled in Roboflow at
original size) into training pieces that look exactly like what the Pi's
model sees:

  photo -> 1280x960 (the Pi's camera frame) -> the full frame + the four
  overlapping 704 px crops of main._inference_regions

A whitefly is a few pixels in the 1280 frame; Roboflow's "Resize to 640"
would shrink it away, so the photos are exported at original size and cut
here instead. Photos are split into train/valid/test by capture burst BEFORE
cutting, so pieces of one photo never land in two splits. Test photos are
also kept whole (1280x960) for an honest real-machine score with evaluate.py.
"""
from __future__ import annotations

import random
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import yaml

FRAME = (1280, 960)
SHARES = {"train": 0.70, "valid": 0.15, "test": 0.15}
NEG_PER_POSITIVE = 2           # empty pieces kept per labelled piece (per split)
NEG_SHARE_OF_SET = 0.25        # ...or up to this share of all labelled images in the split,
                               # so a machine set with (almost) no labels still adds negatives
MIN_INSIDE = 0.5               # share of a box/outline that must be inside a piece


def pi_regions(width=FRAME[0], height=FRAME[1]):
    """main._inference_regions, taken from main.py itself (never a copy)."""
    import offline_test
    return offline_test.backend_definitions(tiles=True)["_inference_regions"](width, height)


def clip_polygon(points, rect):
    """Sutherland-Hodgman: polygon [(x, y)] clipped to rect (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = rect
    edges = [(lambda p: p[0] >= x1, lambda a, b: _cross_x(a, b, x1)),
             (lambda p: p[0] <= x2, lambda a, b: _cross_x(a, b, x2)),
             (lambda p: p[1] >= y1, lambda a, b: _cross_y(a, b, y1)),
             (lambda p: p[1] <= y2, lambda a, b: _cross_y(a, b, y2))]
    out = list(points)
    for inside, cross in edges:
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        if not out:
            break
    return out


def _cross_x(a, b, x):
    t = (x - a[0]) / ((b[0] - a[0]) or 1e-9)
    return (x, a[1] + t * (b[1] - a[1]))


def _cross_y(a, b, y):
    t = (y - a[1]) / ((b[1] - a[1]) or 1e-9)
    return (a[0] + t * (b[0] - a[0]), y)


def _area(points):
    if len(points) < 3:
        return 0.0
    xs, ys = np.array([p[0] for p in points]), np.array([p[1] for p in points])
    return 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))


def read_labels(label_path, class_map, width, height):
    """Roboflow YOLO lines -> [(class_id, [(x, y), ...] in frame pixels, is_box)]."""
    shapes = []
    if not label_path.exists():
        return shapes
    for line in label_path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 5 or int(parts[0]) not in class_map:
            continue
        vals = [float(v) for v in parts[1:]]
        if len(vals) == 4:
            cx, cy, w, h = vals
            pts = [((cx - w / 2) * width, (cy - h / 2) * height), ((cx + w / 2) * width, (cy - h / 2) * height),
                   ((cx + w / 2) * width, (cy + h / 2) * height), ((cx - w / 2) * width, (cy + h / 2) * height)]
            shapes.append((class_map[int(parts[0])], pts, True))
        else:
            shapes.append((class_map[int(parts[0])], list(zip(np.array(vals[0::2]) * width,
                                                               np.array(vals[1::2]) * height)), False))
    return shapes


def piece_labels(shapes, region, as_boxes):
    """Label lines for one piece; shapes mostly outside it are dropped."""
    left, top, right, bottom = region
    w, h = right - left, bottom - top
    lines = []
    for cls, pts, _ in shapes:
        full = _area(pts)
        clipped = clip_polygon(pts, region)
        if full <= 0 or _area(clipped) < MIN_INSIDE * full:
            continue
        local = [((x - left) / w, (y - top) / h) for x, y in clipped]
        if as_boxes:
            xs, ys = [p[0] for p in local], [p[1] for p in local]
            bx1, bx2, by1, by2 = min(xs), max(xs), min(ys), max(ys)
            lines.append(f"{cls} {(bx1 + bx2) / 2:.6f} {(by1 + by2) / 2:.6f} {bx2 - bx1:.6f} {by2 - by1:.6f}")
        else:
            lines.append(f"{cls} " + " ".join(f"{x:.6f} {y:.6f}" for x, y in local))
    return lines


def split_photos(names, seed=7):
    """{name: split} by capture burst (prepare_dataset.capture_groups)."""
    from prepare_dataset import capture_groups
    groups = capture_groups(sorted(names))
    random.Random(seed).shuffle(groups)
    total, filled, plan = len(names), Counter(), {}
    for group in groups:
        split = max(SHARES, key=lambda s: SHARES[s] * total - filled[s])
        filled[split] += len(group)
        for name in group:
            plan[name] = split
    return plan


def add_machine_set(export, dst, test_dst, class_names, as_boxes, seed=7, originals=None, extra=None):
    """Cut a Roboflow export of machine photos into dst/{train,valid} and keep
    the test photos whole in test_dst (its own data.yaml).

    Only machine photos (field_*) are used; other photos in the export belong
    to the base dataset. originals: folder with the full-size field_*.jpg;
    YOLO labels are fractions of the photo, so they fit the original as long
    as Roboflow only shrank it (no crop, no padding).
    extra: {photo stem: [(class_id, [(x, y), ...] in 1280x960 pixels, is_box)]},
    labels added on top of the export's (whiteflies confirmed in
    review_candidates.py on photos marked Null)."""
    export, dst, test_dst = Path(export), Path(dst), Path(test_dst)
    names = yaml.safe_load((export / "data.yaml").read_text())["names"]
    unknown = [n for n in names if n not in class_names and n not in ("whiteflies",)]
    if unknown:
        print(f"   note: classes {unknown} in {export.name} are not used")
    target = {n: i for i, n in enumerate(class_names)}
    class_map = {i: target["whitefly" if n == "whiteflies" else n] for i, n in enumerate(names)
                 if ("whitefly" if n == "whiteflies" else n) in target}
    every = [p for s in ("train", "valid", "test") for p in (export / s / "images").glob("*.jpg")]
    photos = {p.name: p for p in every if p.name.startswith("field_")}
    if len(photos) < len(every):
        print(f"   note: {len(every) - len(photos)} photos in {export.name} are not machine photos; skipped here")
    plan = split_photos(list(photos), seed)
    regions = pi_regions()
    if test_dst.exists():
        shutil.rmtree(test_dst)
    for sub in ("images", "labels"):
        (test_dst / "test" / sub).mkdir(parents=True)
    rng = random.Random(seed)
    stats = Counter()
    empty = {"train": [], "valid": []}
    for name, path in sorted(photos.items()):
        split = plan[name]
        image = cv2.imread(str(path))
        if image is None:
            continue
        original = Path(originals) / (name.split("_jpg.rf.")[0] + ".jpg") if originals else None
        if original is not None and original.exists():
            full = cv2.imread(str(original))
            if full is not None and abs(full.shape[1] / full.shape[0] - image.shape[1] / image.shape[0]) < 0.01:
                image = full
                stats["full-size originals used"] += 1
        if image.shape[1] < FRAME[0]:
            stats["photos smaller than the Pi frame (upscaled)"] += 1
        frame = cv2.resize(image, FRAME, interpolation=cv2.INTER_AREA)
        label = path.parent.parent / "labels" / f"{path.stem}.txt"
        shapes = read_labels(label, class_map, *FRAME)
        added = (extra or {}).get(path.stem.split(".rf.")[0].replace("_jpg", ""), [])
        shapes += added
        stats["reviewed labels added"] += len(added)
        stem = "machine_" + path.stem.split(".rf.")[0].replace("_jpg", "")
        stats[f"{split} photos"] += 1
        stats["labels"] += len(shapes)
        if split == "test":
            cv2.imwrite(str(test_dst / "test" / "images" / f"{stem}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            full = piece_labels(shapes, (0, 0) + FRAME, as_boxes)
            (test_dst / "test" / "labels" / f"{stem}.txt").write_text("\n".join(full) + ("\n" if full else ""))
            continue
        for index, region in enumerate(regions):
            left, top, right, bottom = region
            lines = piece_labels(shapes, region, as_boxes)
            piece = (f"{stem}_r{index}", frame[top:bottom, left:right], lines)
            if lines:
                _write_piece(dst / split, *piece)
                stats[f"{split} labelled pieces"] += 1
            else:
                empty[split].append(piece)
    for split, pieces in empty.items():
        labels_dir = dst / split / "labels"
        labelled = sum(1 for p in labels_dir.glob("*.txt") if p.read_text().strip()) if labels_dir.exists() else 0
        keep = min(len(pieces), max(NEG_PER_POSITIVE * max(1, stats[f"{split} labelled pieces"]),
                                    int(NEG_SHARE_OF_SET * labelled)))
        for piece in rng.sample(pieces, keep):
            _write_piece(dst / split, *piece)
        stats[f"{split} empty pieces"] += keep
    (test_dst / "data.yaml").write_text(yaml.safe_dump(
        {"train": "test/images", "val": "test/images", "test": "test/images",
         "nc": len(class_names), "names": list(class_names)}, sort_keys=False))
    print(f"{export.name} -> {dst.name}: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())) +
          f"; whole test photos -> {test_dst}")
    return stats


def _write_piece(split_dir, stem, image, lines):
    (split_dir / "images").mkdir(parents=True, exist_ok=True)
    (split_dir / "labels").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(split_dir / "images" / f"{stem}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    (split_dir / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))

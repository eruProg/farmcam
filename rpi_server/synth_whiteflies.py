"""Paste real whitefly cut-outs onto machine photos at the true camera size.

Real whiteflies are hard to photograph on the machine (they fly off), and
hand-made composites varied wildly in size (8_jpg had 55 px whiteflies, real
ones are 5-12 px on the Pi's 1280x960 frame). This makes consistent ones:
  - cut-outs: every labelled whitefly in the TRAIN split (test stays clean)
  - backgrounds: labelled camera photos from the TRAIN split, colour-fixed,
    so their existing worm/whitefly labels are kept
  - each pasted whitefly: scaled to a real size, soft-edged, brightness
    matched to the leaf it lands on, slightly blurred like the camera
  - placed only on leaf pixels and away from existing labels

    py synth_whiteflies.py --count 300
    -> ..\\datasets\\synthetic_whiteflies\\images + labels (YOLO, whitefly=0 worm=1)
    then: py prepare_dataset.py --extra-pest ..\\datasets\\synthetic_whiteflies
          (added to train only, never to valid/test)
"""
import argparse
import random
import shutil
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
PEST = HERE.parent / "datasets" / "whiteflies and worms"

# Export images are ~640x480, half the Pi frame: 5-12 px real -> 2.5-6 px here.
SIZE_RANGE = (2.5, 6.5)
PER_IMAGE = (3, 12)


def natural(img, name):
    return np.ascontiguousarray(img[:, :, ::-1]) if "auto_default" in name else img


def yolo_boxes(label_path, w, h):
    out = []
    if label_path.exists():
        for line in label_path.read_text().splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            vals = [float(v) for v in p[1:]]
            if len(vals) == 4:
                cx, cy, bw, bh = vals
                box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
            else:
                xs, ys = vals[0::2], vals[1::2]
                box = (min(xs), min(ys), max(xs), max(ys))
            out.append((int(p[0]), box[0] * w, box[1] * h, box[2] * w, box[3] * h))
    return out


def collect_cutouts(min_px=3):
    cutouts = []
    for img_path in sorted((PEST / "train" / "images").glob("*.jpg")):
        img = None
        for cls, x1, y1, x2, y2 in yolo_boxes(PEST / "train" / "labels" / f"{img_path.stem}.txt", 1, 1):
            if cls != 0:
                continue
            if img is None:
                img = natural(cv2.imread(str(img_path)), img_path.name)
                h, w = img.shape[:2]
            bx1, by1, bx2, by2 = int(x1 * w) - 1, int(y1 * h) - 1, int(np.ceil(x2 * w)) + 1, int(np.ceil(y2 * h)) + 1
            if bx2 - bx1 < min_px or by2 - by1 < min_px or bx1 < 0 or by1 < 0 or bx2 > w or by2 > h:
                continue
            crop = img[by1:by2, bx1:bx2]
            # Keep only bright, pale cut-outs: those are the insect, not background.
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            if np.percentile(hsv[:, :, 2], 90) < 150:
                continue
            cutouts.append(crop.copy())
    return cutouts


def leaf_mask(img):
    """Strongly coloured green only: the white casing and pot have a faint
    green tint from the camera's white balance (saturation ~50-90)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] >= 25) & (hsv[:, :, 0] <= 90) & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 80))
    return cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0


def paste(img, cutout, cx, cy, size, rng):
    h, w = cutout.shape[:2]
    scale = size / max(h, w)
    nw, nh = max(2, int(round(w * scale))), max(2, int(round(h * scale)))
    small = cv2.resize(cutout, (nw, nh), interpolation=cv2.INTER_AREA)
    if rng.random() < 0.5:
        small = small[:, ::-1]
    if rng.random() < 0.5:
        small = cv2.rotate(small, rng.choice([cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE]))
        nh, nw = small.shape[:2]
    x1, y1 = int(cx - nw / 2), int(cy - nh / 2)
    x2, y2 = x1 + nw, y1 + nh
    H, W = img.shape[:2]
    if x1 < 0 or y1 < 0 or x2 > W or y2 > H:
        return None
    region = img[y1:y2, x1:x2].astype(np.float32)
    patch = small.astype(np.float32)
    # Paste only the insect: its own pale (bright, low-saturation) pixels,
    # not the old background around it. Keep its real colours.
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2].astype(np.float32), hsv[:, :, 1]
    insect = ((v >= 0.75 * v.max()) & (s <= 110)).astype(np.float32)
    leaf_v = cv2.cvtColor(img[max(0, y1 - 6):y2 + 6, max(0, x1 - 6):x2 + 6], cv2.COLOR_BGR2HSV)[:, :, 2].mean()
    if insect.sum() < 2 or v[insect > 0].mean() < leaf_v + 15:
        return None                     # not visibly whiter than this leaf: skip
    alpha = cv2.GaussianBlur(insect, (3, 3), 0.6)[:, :, None]
    blended = alpha * patch + (1 - alpha) * region
    img[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)
    return x1, y1, x2, y2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--out", type=Path, default=HERE.parent / "datasets" / "synthetic_whiteflies")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    cutouts = collect_cutouts()
    backgrounds = sorted(p for p in (PEST / "train" / "images").glob("*.jpg") if "auto_default" in p.name)
    if not cutouts or not backgrounds:
        raise SystemExit("Need labelled whiteflies and camera photos in the pest TRAIN split.")
    print(f"{len(cutouts)} whitefly cut-outs, {len(backgrounds)} camera backgrounds")
    if args.out.exists():
        shutil.rmtree(args.out)
    (args.out / "images").mkdir(parents=True)
    (args.out / "labels").mkdir(parents=True)

    made = pasted = 0
    for i in range(args.count):
        bg = rng.choice(backgrounds)
        img = natural(cv2.imread(str(bg)), bg.name)
        h, w = img.shape[:2]
        labels = yolo_boxes(PEST / "train" / "labels" / f"{bg.stem}.txt", w, h)
        mask = leaf_mask(img)
        for _, x1, y1, x2, y2 in labels:                     # keep clear of existing objects
            mask[max(0, int(y1) - 8):int(y2) + 8, max(0, int(x1) - 8):int(x2) + 8] = False
        ys, xs = np.nonzero(mask)
        if len(xs) < 500:
            continue
        lines = [f"{c} {(a + c2) / 2 / w:.6f} {(b + d) / 2 / h:.6f} {(c2 - a) / w:.6f} {(d - b) / h:.6f}"
                 for c, a, b, c2, d in labels]
        for _ in range(rng.randint(*PER_IMAGE)):
            box = None
            for _attempt in range(6):          # a few tries to find a cut-out that shows
                k = rng.randrange(len(xs))
                if mask[ys[k], xs[k]]:
                    box = paste(img, rng.choice(cutouts), xs[k], ys[k], rng.uniform(*SIZE_RANGE), rng)
                if box is not None:
                    break
            if box is None:
                continue
            x1, y1, x2, y2 = box
            mask[max(0, y1 - 10):y2 + 10, max(0, x1 - 10):x2 + 10] = False
            lines.append(f"0 {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}")
            pasted += 1
        # Light camera softness over the whole frame so pasted edges match.
        img = cv2.GaussianBlur(img, (3, 3), rng.uniform(0.3, 0.7))
        name = f"synthwf_{i:04d}"
        cv2.imwrite(str(args.out / "images" / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        (args.out / "labels" / f"{name}.txt").write_text("\n".join(lines) + "\n")
        made += 1
    print(f"Made {made} images with {pasted} pasted whiteflies -> {args.out}")


if __name__ == "__main__":
    main()

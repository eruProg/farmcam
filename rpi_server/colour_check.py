"""Tell whether a FarmCam photo has its red and blue channels reversed.

An old capture path ran cv2.COLOR_RGB2BGR on frames Picamera2 already delivers
as BGR, so its photos (named img_auto_default_* / auto_default_*) show teal
leaves, blue worms and blue lesions. Everything in the current datasets with
that name is swapped, so the filename is the reliable signal.

Hue is only a fallback for other files: swapping red and blue mirrors hue
around pure green (60 in OpenCV units). Lettuce is yellow-green (below 60) in
real life, but dark or bluish varieties sit near 60, so near the line the
answer is "unsure" (None).

    python colour_check.py <folder>      # prints swapped / natural / unsure
"""
import sys
from pathlib import Path

import cv2
import numpy as np

SWAPPED_NAME_MARKERS = ("auto_default",)
LEAF_HUE_RANGE = (25, 100)   # covers real (lime) and swapped (teal) leaves
UNSURE_BAND = (54, 64)       # median leaf hue inside this band can't be judged
MIN_LEAF_FRACTION = 0.03


def leaf_hue(img_bgr):
    """Median hue of saturated leaf-coloured pixels, or None if too few."""
    small = cv2.resize(img_bgr, (320, max(1, int(320 * img_bgr.shape[0] / img_bgr.shape[1]))),
                       interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    leaf = (s > 60) & (v > 50) & (h >= LEAF_HUE_RANGE[0]) & (h <= LEAF_HUE_RANGE[1])
    if leaf.mean() < MIN_LEAF_FRACTION:
        return None
    return float(np.median(h[leaf]))


def is_swapped(img_bgr, name=None):
    """True = swapped, False = natural, None = can't tell."""
    if name and any(marker in str(name) for marker in SWAPPED_NAME_MARKERS):
        return True
    hue = leaf_hue(img_bgr)
    if hue is None or UNSURE_BAND[0] <= hue <= UNSURE_BAND[1]:
        return None
    return hue > UNSURE_BAND[1]


def to_natural(img_bgr, name=None, assume=None):
    """Return natural colours. `assume` (True/False) overrides detection."""
    swapped = assume if assume is not None else is_swapped(img_bgr, name)
    return img_bgr[:, :, ::-1].copy() if swapped else img_bgr


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    tally = {True: 0, False: 0, None: 0}
    for path in paths:
        img = cv2.imread(str(path))
        if img is None:
            continue
        result, hue = is_swapped(img, path.name), leaf_hue(img)
        tally[result] += 1
        label = {True: "swapped", False: "natural", None: "unsure"}[result]
        print(f"{label:8s} hue={'-' if hue is None else f'{hue:5.1f}'}  {path.name}")
    print(f"\nswapped={tally[True]} natural={tally[False]} unsure={tally[None]}")

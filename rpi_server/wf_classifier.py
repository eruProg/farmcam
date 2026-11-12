"""Second deep-learning stage for whiteflies.

Stage 1 (YOLOv8 on the Hailo chip) finds whitefly candidates in the 1280x960
frame, where a whitefly is a 5-12 px speck. Stage 2 is a small CNN
(YOLOv8n-cls, trained by make_wf_crops.py + the Colab notebook) that looks at
each candidate again on a sharp crop of the camera's 4056x3040 main stream
(about 3x more pixels across) and answers "whitefly or not". It replaces the
hand-written colour rules (green ring, pale, contrast) for whiteflies.

crop_window() is shared by training and the Pi, so both see identical crops.
Without the model file the classifier is inactive and nothing changes.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np

LORES = (1280, 960)          # the frame stage 1 runs on (box coordinates)
CROP = 96                    # classifier input, pixels
WINDOW_FACTOR = 3.0          # window = 3x the box, so the leaf around it shows
MIN_WINDOW_LORES = 20        # ~63 px of the 4056 frame: a speck still gets context
CLASSES = ("not_whitefly", "whitefly")   # Ultralytics sorts class folders A-Z


def crop_window(box: Sequence[float], image: np.ndarray,
                scale: Optional[Sequence[float]] = None) -> np.ndarray:
    """Square crop around a lores box (x1, y1, x2, y2 in 1280x960 pixels),
    cut from `image`: the 1280x960 frame itself or the 4056x3040 main frame
    (same field of view). Shifted, never shrunk, to stay inside the image.
    scale: image pixels per lores pixel, for images that are not 4:3."""
    h, w = image.shape[:2]
    sx, sy = scale if scale is not None else (w / LORES[0], h / LORES[1])
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    side_lores = max(MIN_WINDOW_LORES, WINDOW_FACTOR * max(x2 - x1, y2 - y1))
    side = int(round(min(side_lores * sx, w, h)))
    cx, cy = (x1 + x2) / 2 * sx, (y1 + y2) / 2 * sy
    left = int(round(min(max(cx - side / 2, 0), w - side)))
    top = int(round(min(max(cy - side / 2, 0), h - side)))
    window = image[top:top + side, left:left + side]
    interpolation = cv2.INTER_AREA if side >= CROP else cv2.INTER_CUBIC
    return cv2.resize(window, (CROP, CROP), interpolation=interpolation)


class WhiteflyClassifier:
    """`scores(crops)` -> probability of "whitefly" for each BGR crop.
    OpenCV's DNN module runs it on the Pi's CPU (a few ms per crop)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._net = cv2.dnn.readNetFromONNX(str(self.path))
        self._lock = threading.Lock()        # the live overlay and the verifier share it

    @property
    def active(self) -> bool:
        return True

    def scores(self, crops: List[np.ndarray]) -> List[float]:
        out = []
        with self._lock:
            for crop in crops:
                blob = cv2.dnn.blobFromImage(crop, 1 / 255.0, (CROP, CROP), swapRB=True)
                self._net.setInput(blob)
                probs = np.asarray(self._net.forward(), dtype=np.float32).reshape(-1)
                if probs.size != len(CLASSES):
                    raise ValueError(f"{self.path.name}: expected {len(CLASSES)} classes, got {probs.size}")
                if not np.isclose(probs.sum(), 1.0, atol=1e-3):      # raw logits: softmax them
                    probs = np.exp(probs - probs.max())
                    probs /= probs.sum()
                out.append(float(probs[CLASSES.index("whitefly")]))
        return out


def load(path) -> Optional[WhiteflyClassifier]:
    """The classifier, or None when the model file is missing or unreadable
    (the Pi then keeps the colour rules, exactly as before)."""
    if not path or not Path(path).is_file():
        return None
    try:
        return WhiteflyClassifier(Path(path))
    except Exception as exc:                 # a broken file must never stop detection
        print(f"⚠️  Whitefly classifier not loaded ({exc}); using the colour checks")
        return None

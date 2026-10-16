"""
verifier.py — Five-gate detection verifier
==========================================
All logic runs DOWNSTREAM of the frozen detect_frame() output and
UPSTREAM of the persistence / actuator layer.  Nothing here touches
the Hailo pipeline, HEF files, or label map.

Gates applied in order:
    1. Confidence     — per-class threshold from config
    2. Size & shape   — bbox area and aspect-ratio range per class
    3. Skin (bbox)    — YCrCb pixel fraction inside the bbox
    3b. Skin (frame)  — fast downscale check; suppresses all pests
    4. Context        — green ring + brightness/saturation (whitefly only)
    5. Temporal       — centroid tracker; confirms only stationary, repeated sightings

Rejected detections are saved as annotated JPEG + JSON sidecar in
config.REJECTED_DIR for later inspection with tune_verifier.py.

Usage:
    from verifier import DetectionVerifier
    from scan_state import FrameMeta

    verifier = DetectionVerifier()
    confirmed = verifier.verify(raw_dets, frame_bgr, frame_meta)
    # confirmed: list of [x1,y1,x2,y2, class_name, confidence]
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

import config

log = logging.getLogger("farmcam.verifier")

# Detection tuple indices
_X1, _Y1, _X2, _Y2, _NAME, _CONF = 0, 1, 2, 3, 4, 5
_PEST_CLASSES = frozenset({"whitefly", "whiteflies", "worm"})
_WHITEFLY_CLASSES = frozenset({"whitefly", "whiteflies"})


# ── Public interface ──────────────────────────────────────────────────────────

class DetectionVerifier:
    """
    Thread-safe (one verifier per detect_loop thread).
    The internal _CentroidTracker is NOT shared across stations; call
    reset_tracker() when the station changes.
    """

    def __init__(
        self,
        rejected_dir: Optional[Path] = None,
        clock_fn=None,
    ) -> None:
        self._rejected_dir = Path(rejected_dir or config.REJECTED_DIR)
        self._rejected_dir.mkdir(parents=True, exist_ok=True)
        self._clock      = clock_fn or time.monotonic
        self._tracker    = _CentroidTracker(clock_fn=self._clock)

    # ── Main entry point ─────────────────────────────────────────────────

    def verify(
        self,
        dets:       List,         # raw output from detect_frame()
        frame_bgr:  np.ndarray,
        frame_meta,               # FrameMeta from scan_state
    ) -> List:
        """
        Returns a list of confirmed detections in the same format as dets.
        Side-effect: saves rejected crops to REJECTED_DIR.
        """
        if dets is None or len(dets) == 0:
            self._tracker.tick([], frame_meta)
            return []

        h, w = frame_bgr.shape[:2]

        # Gate 3b — frame-level skin check (fast, run once per frame)
        skin_frame_pct = _skin_frame_ratio(frame_bgr)
        suppress_pests = skin_frame_pct > config.SKIN_FRAME_MAX_PCT
        if suppress_pests:
            log.debug("hand_in_frame station=%s position=%s skin_pct=%.2f",
                      frame_meta.station, frame_meta.position, skin_frame_pct)

        # ROI gate (normalised coordinates)
        roi = config.STATION_ROIS.get(
            (frame_meta.station, frame_meta.position), [0, 0, 1, 1]
        )

        candidates: List = []
        for det in dets:
            x1, y1, x2, y2 = int(det[_X1]), int(det[_Y1]), int(det[_X2]), int(det[_Y2])
            name = str(det[_NAME])
            conf = float(det[_CONF])

            # ROI gate
            cx = ((x1 + x2) / 2) / max(w, 1)
            cy = ((y1 + y2) / 2) / max(h, 1)
            if not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]):
                self._save_rejected(frame_bgr, x1, y1, x2, y2,
                                    name, conf, "roi", frame_meta)
                continue

            reason = self._check_gates(
                x1, y1, x2, y2, name, conf,
                frame_bgr, w, h, suppress_pests, frame_meta
            )
            if reason:
                self._save_rejected(frame_bgr, x1, y1, x2, y2,
                                    name, conf, reason, frame_meta)
            else:
                candidates.append(det)

        # Gate 5 — temporal confirmation
        confirmed = self._tracker.tick(candidates, frame_meta)
        return confirmed

    def reset_tracker(self) -> None:
        """Call when the station changes to clear stale tracks."""
        self._tracker.reset()

    # ── Gate evaluator ────────────────────────────────────────────────────

    def _check_gates(
        self,
        x1: int, y1: int, x2: int, y2: int,
        name: str, conf: float,
        frame_bgr: np.ndarray,
        w: int, h: int,
        suppress_pests: bool,
        frame_meta,
    ) -> Optional[str]:
        """
        Returns None if all gates pass, or a short reason string if rejected.
        Short strings become part of the rejected filename.
        """
        # ── Gate 1: confidence ────────────────────────────────────────────
        thr = config.PER_CLASS_THRESHOLDS.get(name, config.CONFIDENCE_THRESHOLD)
        if conf < thr:
            return f"conf_{conf:.2f}_lt_{thr:.2f}"

        # ── Gate 2: size and shape ────────────────────────────────────────
        bw    = x2 - x1
        bh    = y2 - y1
        area  = bw * bh
        ar    = bw / max(bh, 1)

        size_spec = config.PER_CLASS_SIZE.get(
            name,
            (config.MIN_BOX_AREA, 999_999, config.MIN_ASPECT_RATIO, config.MAX_ASPECT_RATIO)
        )
        a_min, a_max, ar_min, ar_max = size_spec

        if area < a_min:
            return f"area_{area}_lt_{a_min}"
        if area > a_max:
            return f"area_{area}_gt_{a_max}"
        if name not in ("fungus",) and ar < ar_min:
            return f"ar_{ar:.2f}_lt_{ar_min}"
        if name not in ("fungus",) and ar > ar_max:
            return f"ar_{ar:.2f}_gt_{ar_max}"

        # ── Gates 3 & 4: pest-specific ────────────────────────────────────
        if name in _PEST_CLASSES:

            # Gate 3: skin rejection — frame level
            if suppress_pests:
                return "hand_in_frame"

            # Gate 3: skin rejection — bbox crop
            crop = _padded_crop(frame_bgr, x1, y1, x2, y2, pad=4)
            if crop.size > 0 and _skin_ratio(crop) > config.SIGMA_MAX:
                return f"skin_gt_{config.SIGMA_MAX}"

            # Gate 4: whitefly context
            if name in _WHITEFLY_CLASSES:
                ring = _ring_crop(frame_bgr, x1, y1, x2, y2, ring=16)
                if ring.size == 0:
                    return "ring_empty"
                if _green_ratio(ring) < config.GREEN_RATIO_MIN:
                    return f"green_lt_{config.GREEN_RATIO_MIN:.2f}"
                hsv       = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                mean_v    = float(np.mean(hsv[:, :, 2]))
                mean_s    = float(np.mean(hsv[:, :, 1]))
                if mean_v < config.BRIGHT_V_MIN:
                    return f"v_{mean_v:.0f}_lt_{config.BRIGHT_V_MIN}"
                if mean_s > config.DARK_S_MAX:
                    return f"s_{mean_s:.0f}_gt_{config.DARK_S_MAX}"

        return None   # all gates passed

    # ── Rejected saver ────────────────────────────────────────────────────

    def _save_rejected(
        self,
        frame_bgr: np.ndarray,
        x1: int, y1: int, x2: int, y2: int,
        name: str, conf: float,
        reason: str,
        frame_meta,
    ) -> None:
        try:
            # Sanitise reason for use in filename
            safe_reason = reason[:40].replace("/", "_").replace(" ", "_")
            stem = f"{name}_{safe_reason}_{uuid.uuid4().hex[:6]}"
            img_path  = self._rejected_dir / f"{stem}.jpg"
            meta_path = self._rejected_dir / f"{stem}.json"

            annotated = frame_bgr.copy()
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
            label = f"{name} [{reason}]"
            cv2.putText(annotated, label, (max(0, x1), max(10, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
            cv2.imwrite(str(img_path), annotated,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])

            meta = {
                "reason":   reason,
                "class":    name,
                "conf":     conf,
                "bbox":     [x1, y1, x2, y2],
                "station":  frame_meta.station,
                "position": frame_meta.position,
                "scan_id":  frame_meta.scan_id,
                "seq":      frame_meta.seq,
            }
            meta_path.write_text(json.dumps(meta, indent=2))
        except Exception as exc:
            log.debug("save_rejected failed: %s", exc)


# ── Centroid tracker (Gate 5) ─────────────────────────────────────────────────

class _CentroidTracker:
    """
    Lightweight IoU + centroid tracker.
    A candidate is confirmed when:
      - Its track has existed for >= T_MIN_SECS, OR
      - It was seen in >= TRACK_K of the last TRACK_N frames.
    Tracks whose centroid drifts faster than CENTROID_DRIFT_MAX px/s are
    rejected (moving finger / vibration artifact).
    """

    def __init__(self, clock_fn=None) -> None:
        self._clock   = clock_fn or time.monotonic
        self._tracks: dict = {}   # track_id -> _Track
        self._next_id = 0

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 0

    def tick(self, candidates: List, frame_meta) -> List:
        now     = frame_meta.captured_at
        matched = set()

        for det in candidates:
            x1, y1, x2, y2 = int(det[_X1]), int(det[_Y1]), int(det[_X2]), int(det[_Y2])
            name  = str(det[_NAME])
            conf  = float(det[_CONF])
            bbox  = (x1, y1, x2, y2)
            cx    = (x1 + x2) / 2
            cy    = (y1 + y2) / 2

            best_tid   = None
            best_score = 0.0
            for tid, tr in self._tracks.items():
                if tr.name != name or tid in matched or tr.scan_id != frame_meta.scan_id:
                    continue
                iou = _iou(bbox, tr.bbox)
                if iou < 0.05:
                    # Centroid-distance fallback
                    dist = ((cx - tr.cx) ** 2 + (cy - tr.cy) ** 2) ** 0.5
                    if dist > config.CENTROID_DRIFT_MAX * 3:
                        continue
                    score = max(0.0, 1.0 - dist / (config.CENTROID_DRIFT_MAX * 3))
                else:
                    score = iou
                if score > best_score:
                    best_score = score
                    best_tid   = tid

            if best_tid is None:
                # New track
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = _Track(
                    name=name, bbox=bbox, cx=cx, cy=cy,
                    first_seen=now, last_seen=now,
                    times=[now], confs=[conf],
                    scan_id=frame_meta.scan_id,
                )
                matched.add(tid)
            else:
                tr = self._tracks[best_tid]
                dt = now - tr.last_seen
                if dt > 0:
                    drift = ((cx - tr.cx) ** 2 + (cy - tr.cy) ** 2) ** 0.5 / dt
                    if drift > config.CENTROID_DRIFT_MAX:
                        # Moving too fast — reject this candidate
                        self._save_note(best_tid, f"drift {drift:.1f} px/s")
                        continue
                tr.bbox      = bbox
                tr.cx        = cx
                tr.cy        = cy
                tr.last_seen = now
                tr.times.append(now)
                tr.confs.append(conf)
                matched.add(best_tid)

        # Purge stale tracks (not seen in this or last few frames)
        stale_cutoff = now - max(config.TRACK_N * 0.15, 1.5)
        self._tracks = {
            tid: tr for tid, tr in self._tracks.items()
            if tr.last_seen >= stale_cutoff
        }

        # Confirm tracks that pass temporal criterion
        confirmed: List = []
        for tid, tr in self._tracks.items():
            if tid not in matched:
                continue
            duration = tr.last_seen - tr.first_seen
            recent   = [t for t in tr.times if now - t < config.TRACK_N * 0.15]
            if duration >= config.T_MIN_SECS or len(recent) >= config.TRACK_K:
                x1, y1, x2, y2 = tr.bbox
                avg_conf = float(np.mean(tr.confs[-config.TRACK_N:]))
                # Rebuild a det-compatible list: [x1,y1,x2,y2,name,conf,...]
                out = list(det) if (det := self._find_det(candidates, tr)) else \
                      [x1, y1, x2, y2, tr.name, avg_conf]
                confirmed.append(out)

        return confirmed

    def _find_det(self, candidates, tr):
        """Return the original det tuple for a confirmed track (preserves extras)."""
        for det in candidates:
            if str(det[_NAME]) == tr.name:
                x1, y1, x2, y2 = int(det[_X1]), int(det[_Y1]), int(det[_X2]), int(det[_Y2])
                if _iou((x1, y1, x2, y2), tr.bbox) > 0.3:
                    return det
        return None

    def _save_note(self, tid, note: str) -> None:
        log.debug("tracker tid=%d rejected: %s", tid, note)


class _Track:
    __slots__ = ("name", "bbox", "cx", "cy",
                 "first_seen", "last_seen", "times", "confs", "scan_id")

    def __init__(self, name, bbox, cx, cy,
                 first_seen, last_seen, times, confs, scan_id):
        self.name       = name
        self.bbox       = bbox
        self.cx         = cx
        self.cy         = cy
        self.first_seen = first_seen
        self.last_seen  = last_seen
        self.times      = times
        self.confs      = confs
        self.scan_id    = scan_id


# ── Image helpers ─────────────────────────────────────────────────────────────

def _skin_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of pixels in YCrCb skin range."""
    if crop_bgr.size == 0:
        return 0.0
    ycrcb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb)
    mask = (
        (ycrcb[:, :, 1] >= config.SKIN_CR_MIN) &
        (ycrcb[:, :, 1] <= config.SKIN_CR_MAX) &
        (ycrcb[:, :, 2] >= config.SKIN_CB_MIN) &
        (ycrcb[:, :, 2] <= config.SKIN_CB_MAX)
    )
    return float(np.sum(mask)) / max(1, mask.size)


def _skin_frame_ratio(frame_bgr: np.ndarray) -> float:
    """Quick frame-level skin check on 160×120 downscale."""
    small = cv2.resize(frame_bgr, (160, 120), interpolation=cv2.INTER_NEAREST)
    return _skin_ratio(small)


def _green_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of pixels in HSV green range (hue 35-85, sat > 50)."""
    if crop_bgr.size == 0:
        return 0.0
    hsv  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) &
        (hsv[:, :, 1].astype(np.int32) > 50)
    )
    return float(np.sum(mask)) / max(1, mask.size)


def _padded_crop(
    frame: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    pad: int = 4,
) -> np.ndarray:
    h, w = frame.shape[:2]
    return frame[
        max(0, y1 - pad): min(h, y2 + pad),
        max(0, x1 - pad): min(w, x2 + pad),
    ]


def _ring_crop(
    frame: np.ndarray,
    x1: int, y1: int, x2: int, y2: int,
    ring: int = 16,
) -> np.ndarray:
    """Pixels in an annular ring around the bbox (excludes interior)."""
    h, w = frame.shape[:2]
    bx1, by1 = max(0, x1 - ring), max(0, y1 - ring)
    bx2, by2 = min(w, x2 + ring), min(h, y2 + ring)
    outer = frame[by1:by2, bx1:bx2].copy()
    # Zero out the inner bbox so it doesn't pollute the green ratio
    ix1 = x1 - bx1; iy1 = y1 - by1
    ix2 = x2 - bx1; iy2 = y2 - by1
    outer[max(0, iy1):iy2, max(0, ix1):ix2] = 0
    return outer


def _iou(a: Tuple[int, ...], b: Tuple[int, ...]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / max(ua, 1)

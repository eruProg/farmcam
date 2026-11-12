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
        classifier=None,
        zoom=None,
    ) -> None:
        self._rejected_dir = Path(rejected_dir or config.REJECTED_DIR)
        self._rejected_dir.mkdir(parents=True, exist_ok=True)
        self._clock      = clock_fn or time.monotonic
        self._tracker    = _CentroidTracker(clock_fn=self._clock)
        self._last_saved: dict = {}   # (class, gate) -> last save time
        # Second deep-learning stage (wf_classifier.py). `zoom(boxes)` returns
        # sharp crops from the 4056x3040 main stream, or None (then the crops
        # come from the 1280x960 frame itself).
        self.classifier = classifier
        self.zoom = zoom

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

        candidates = self._gate_frame(dets, frame_bgr, frame_meta, save=True, zoom=True)

        # Gate 5 — temporal confirmation
        confirmed = self._tracker.tick(candidates, frame_meta)
        return confirmed

    def filter_static(self, dets: List, frame_bgr: np.ndarray, frame_meta) -> List:
        """Gates 1-4 only (no tracker, no saving) — for the live overlay."""
        if dets is None or len(dets) == 0:
            return []
        return self._gate_frame(dets, frame_bgr, frame_meta, save=False)

    def explain(self, dets: List, frame_bgr: np.ndarray, frame_meta) -> List:
        """[(det, reason or None)] for every det, gates 1-4 — offline testing."""
        if dets is None or len(dets) == 0:
            return []
        return self._gate_frame(dets, frame_bgr, frame_meta, save=False, explain=True, zoom=True)

    def _gate_frame(self, dets: List, frame_bgr: np.ndarray, frame_meta, save: bool,
                    explain: bool = False, zoom: bool = False) -> List:
        h, w = frame_bgr.shape[:2]
        station = getattr(frame_meta, "station", None)
        position = getattr(frame_meta, "position", None)

        # ROI gate (normalised coordinates)
        roi = config.STATION_ROIS.get((station, position), [0, 0, 1, 1])

        # Gate 3b — hand check: one big skin blob inside the ROI (once per frame)
        skin_blob_pct = _skin_blob_ratio(frame_bgr, roi)
        suppress_pests = skin_blob_pct > config.SKIN_FRAME_MAX_PCT
        if suppress_pests:
            log.debug("hand_in_frame station=%s position=%s skin_blob=%.2f",
                      station, position, skin_blob_pct)

        # Worm candidates, including ones too weak to count, veto fungus on
        # the same spot: the fungus-only SEG model scores worms as fungus.
        worm_boxes = [tuple(int(v) for v in det[:4]) for det in dets
                      if str(det[_NAME]) == "worm"
                      and float(det[_CONF]) >= config.WORM_HINT_MIN_CONF]

        cnn = self.classifier is not None
        pending: List[int] = []       # whiteflies waiting for the CNN (index in explained)
        candidates: List = []
        explained: List = []
        for det in dets:
            x1, y1, x2, y2 = int(det[_X1]), int(det[_Y1]), int(det[_X2]), int(det[_Y2])
            name = str(det[_NAME])
            conf = float(det[_CONF])

            # ROI gate
            cx = ((x1 + x2) / 2) / max(w, 1)
            cy = ((y1 + y2) / 2) / max(h, 1)
            if name in getattr(config, "IGNORED_CLASSES", ()):
                reason = name          # e.g. "dirt": learned so it is NOT called fungus
            elif not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]):
                reason = "roi"
            elif name == "fungus" and _covers_worm((x1, y1, x2, y2), worm_boxes):
                reason = "worm_overlap"
            else:
                reason = self._check_gates(
                    x1, y1, x2, y2, name, conf,
                    frame_bgr, w, h, suppress_pests, frame_meta,
                    cnn=cnn and name in _WHITEFLY_CLASSES,
                )
            explained.append((det, reason))
            if not reason and cnn and name in _WHITEFLY_CLASSES:
                pending.append(len(explained) - 1)     # decided below, in one batch
                continue
            if not reason:
                candidates.append(det)
            # Weak worms are fungus vetoes only, not rejections worth saving.
            elif save and not (name == "worm" and reason.startswith("conf")) \
                    and name not in getattr(config, "IGNORED_CLASSES", ()):
                self._save_rejected(frame_bgr, x1, y1, x2, y2,
                                    name, conf, reason, frame_meta)

        if pending:
            for i, p in zip(pending, self._whitefly_scores([explained[i][0] for i in pending],
                                                            frame_bgr, zoom)):
                det = explained[i][0]
                if p >= config.WF_CLS_MIN:
                    candidates.append(det)
                    continue
                reason = f"cnn_{p:.2f}"
                explained[i] = (det, reason)
                if save:
                    self._save_rejected(frame_bgr, int(det[_X1]), int(det[_Y1]), int(det[_X2]),
                                        int(det[_Y2]), str(det[_NAME]), float(det[_CONF]), reason, frame_meta)

        # Fungus needs company: one lone lesion in a frame is not reported.
        min_fungus = getattr(config, "FUNGUS_MIN_PER_FRAME", 1)
        lone = [i for i, (det, reason) in enumerate(explained)
                if reason is None and str(det[_NAME]) == "fungus"]
        if 0 < len(lone) < min_fungus:
            for i in lone:
                explained[i] = (explained[i][0], "fungus_alone")
            candidates = [det for det in candidates if str(det[_NAME]) != "fungus"]
        return explained if explain else candidates

    def reset_tracker(self) -> None:
        """Call when the station changes to clear stale tracks."""
        self._tracker.reset()

    def _whitefly_scores(self, dets: List, frame_bgr: np.ndarray, zoom: bool) -> List[float]:
        """CNN probability per whitefly candidate. One zoom request fetches all
        their sharp crops; without it (live overlay, timeout, PC) the crops
        come from the 1280x960 frame."""
        from wf_classifier import crop_window
        boxes = [tuple(float(v) for v in det[:4]) for det in dets]
        crops = None
        if zoom and self.zoom is not None:
            try:
                crops = self.zoom(boxes)
            except Exception:
                log.exception("whitefly zoom failed; using the 1280x960 frame")
        if not crops or len(crops) != len(boxes):
            crops = [crop_window(box, frame_bgr) for box in boxes]
        return self.classifier.scores(crops)

    # ── Gate evaluator ────────────────────────────────────────────────────

    def _check_gates(
        self,
        x1: int, y1: int, x2: int, y2: int,
        name: str, conf: float,
        frame_bgr: np.ndarray,
        w: int, h: int,
        suppress_pests: bool,
        frame_meta,
        cnn: bool = False,
    ) -> Optional[str]:
        """
        Returns None if all gates pass, or a short reason string if rejected.
        Short strings become part of the rejected filename.
        cnn: a whitefly the second deep-learning stage decides on. It starts at
        the lower WF_STAGE1_MIN and skips the colour rules; the CNN replaces them.
        """
        # ── Gate 1: confidence ────────────────────────────────────────────
        thr = (config.WF_STAGE1_MIN if cnn
               else config.PER_CLASS_THRESHOLDS.get(name, config.CONFIDENCE_THRESHOLD))
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

        # ── Gate 4: fungus context — must sit on a leaf ───────────────────
        if name == "fungus":
            # Wider hue range than whitefly: yellowing, infected leaves count.
            leaf = _green_ratio(_padded_crop(frame_bgr, x1, y1, x2, y2, pad=16),
                                h_min=25, h_max=90)
            if leaf < config.FUNGUS_LEAF_GREEN_MIN:
                return f"leaf_{leaf:.2f}_lt_{config.FUNGUS_LEAF_GREEN_MIN:.2f}"
            box = _padded_crop(frame_bgr, x1, y1, x2, y2, pad=0)
            glare = _glare_ratio(box)
            if glare > config.GLARE_FRAC_MAX:
                return f"glare_{glare:.2f}_gt_{config.GLARE_FRAC_MAX:.2f}"
            plant = _plant_ratio(box)
            if plant < config.FUNGUS_INTERIOR_LEAF_MIN:
                return f"offleaf_{plant:.2f}_lt_{config.FUNGUS_INTERIOR_LEAF_MIN:.2f}"

        # ── Gates 3 & 4: pest-specific ────────────────────────────────────
        if name in _PEST_CLASSES:

            # Gate 3: skin rejection — frame level
            if suppress_pests:
                return "hand_in_frame"

            # The CNN sees the sharp crop itself: no colour rules below.
            if cnn:
                return None

            # Gate 3: skin rejection - bbox crop (whiteflies only; worms are naturally flesh/tan/brown)
            if name in _WHITEFLY_CLASSES:
                crop = _padded_crop(frame_bgr, x1, y1, x2, y2, pad=4)
                if crop.size > 0 and _skin_ratio(crop) > config.SIGMA_MAX:
                    return f"skin_gt_{config.SIGMA_MAX}"
            # Gate 4: worm context - a worm sits on the lettuce, so the band
            # around its box is mostly plant. Rejects long dark background
            # things (the black pipe, a shadow on the wall) called "worm".
            if name == "worm" and getattr(config, "WORM_RING_PLANT_MIN", 0) > 0:
                plant = _ring_plant_ratio(frame_bgr, x1, y1, x2, y2, ring=24)
                if plant < config.WORM_RING_PLANT_MIN:
                    return f"worm_offplant_{plant:.2f}_lt_{config.WORM_RING_PLANT_MIN:.2f}"
            # Gate 4: whitefly context
            if name in _WHITEFLY_CLASSES:
                ring = _ring_crop(frame_bgr, x1, y1, x2, y2, ring=16)
                if ring.size == 0:
                    return "ring_empty"
                if _green_ratio(ring) < config.GREEN_RATIO_MIN:
                    return f"green_lt_{config.GREEN_RATIO_MIN:.2f}"
                # Count pale pixels inside the box; a mean over a small box
                # is dominated by the green leaf around the insect.
                box = _padded_crop(frame_bgr, x1, y1, x2, y2, pad=0)
                pale = _pale_ratio(box)
                if pale < config.WF_PALE_FRAC_MIN:
                    return f"pale_{pale:.2f}_lt_{config.WF_PALE_FRAC_MIN:.2f}"
                contrast = _contrast_vs_ring(frame_bgr, x1, y1, x2, y2, ring=16)
                if contrast < config.WF_CONTRAST_MIN:
                    return f"contrast_{contrast:.0f}_lt_{config.WF_CONTRAST_MIN}"

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
        # Rate-limit per class+gate so a persistent false box can't fill the SD card.
        key = (name, reason.split("_")[0])
        now = self._clock()
        if now - self._last_saved.get(key, float("-inf")) < config.REJECTED_SAVE_INTERVAL_S:
            return
        self._last_saved[key] = now
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
      - It was seen in >= TRACK_K of the last TRACK_N processed frames.
    Counting frames (not seconds) matters: DET+SEG over five regions runs
    at only a few frames per second, so "3 sightings within 0.75 s" was
    almost never reachable and real whiteflies never confirmed.
    Tracks whose centroid drifts faster than CENTROID_DRIFT_MAX px/s are
    rejected (moving finger / vibration artifact).
    """

    def __init__(self, clock_fn=None) -> None:
        self._clock   = clock_fn or time.monotonic
        self._tracks: dict = {}   # track_id -> _Track
        self._next_id = 0
        self._frame   = 0         # processed-frame counter

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 0
        self._frame   = 0

    def tick(self, candidates: List, frame_meta) -> List:
        now     = frame_meta.captured_at
        self._frame += 1
        frame   = self._frame
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
                    scan_id=frame_meta.scan_id, frames=[frame],
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
                tr.frames.append(frame)
                del tr.frames[:-config.TRACK_N]
                matched.add(best_tid)

        # Purge stale tracks (missed for more than TRACK_N processed frames)
        self._tracks = {
            tid: tr for tid, tr in self._tracks.items()
            if tr.frames[-1] > frame - config.TRACK_N
        }

        # Confirm tracks that pass temporal criterion
        confirmed: List = []
        for tid, tr in self._tracks.items():
            if tid not in matched:
                continue
            duration = tr.last_seen - tr.first_seen
            recent   = [f for f in tr.frames if f > frame - config.TRACK_N]
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
                 "first_seen", "last_seen", "times", "confs", "scan_id", "frames")

    def __init__(self, name, bbox, cx, cy,
                 first_seen, last_seen, times, confs, scan_id, frames):
        self.name       = name
        self.bbox       = bbox
        self.cx         = cx
        self.cy         = cy
        self.first_seen = first_seen
        self.last_seen  = last_seen
        self.times      = times
        self.confs      = confs
        self.scan_id    = scan_id
        self.frames     = frames


# ── Image helpers ─────────────────────────────────────────────────────────────

def _skin_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of pixels in YCrCb skin range."""
    if crop_bgr.size == 0:
        return 0.0
    ycrcb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb)
    mask = (
        (ycrcb[:, :, 0] >= config.SKIN_Y_MIN) &
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


def _skin_mask(crop_bgr: np.ndarray) -> np.ndarray:
    ycrcb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb)
    return ((ycrcb[:, :, 0] >= config.SKIN_Y_MIN) & (ycrcb[:, :, 1] >= config.SKIN_CR_MIN) & (ycrcb[:, :, 1] <= config.SKIN_CR_MAX) &
            (ycrcb[:, :, 2] >= config.SKIN_CB_MIN) & (ycrcb[:, :, 2] <= config.SKIN_CB_MAX))


def _skin_blob_ratio(frame_bgr: np.ndarray, roi) -> float:
    """Largest connected skin blob inside the ROI, as a fraction of the ROI.
    A hand is one solid blob; reddish background is scattered pixels."""
    h, w = frame_bgr.shape[:2]
    rx1, ry1 = int(roi[0] * w), int(roi[1] * h)
    rx2, ry2 = max(rx1 + 1, int(roi[2] * w)), max(ry1 + 1, int(roi[3] * h))
    small = cv2.resize(frame_bgr[ry1:ry2, rx1:rx2], (160, 120), interpolation=cv2.INTER_NEAREST)
    mask = _skin_mask(small).astype(np.uint8)
    # Close small gaps so a real hand stays one component.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0
    return float(stats[1:, cv2.CC_STAT_AREA].max()) / mask.size


def _glare_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of bright, colourless pixels (glare, droplets, white casing)."""
    if crop_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 2] >= config.GLARE_V_MIN) & (hsv[:, :, 1] <= config.GLARE_S_MAX)
    return float(np.sum(mask)) / max(1, mask.size)


def _plant_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of leaf (green) or lesion (brown/yellow) pixels."""
    if crop_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1].astype(np.int32), hsv[:, :, 2]
    leaf = (h >= 25) & (h <= 90) & (s > 50)
    lesion = (h >= 5) & (h < 25) & (s > 50) & (v > 40)
    return float(np.sum(leaf | lesion)) / max(1, h.size)


def _ring_plant_ratio(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                      ring: int = 24) -> float:
    """Share of leaf/lesion-coloured pixels in the band around the box only
    (the box itself is left out, so a big box does not dilute it)."""
    h, w = frame.shape[:2]
    bx1, by1 = max(0, x1 - ring), max(0, y1 - ring)
    bx2, by2 = min(w, x2 + ring), min(h, y2 + ring)
    crop = frame[by1:by2, bx1:bx2]
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[:, :, 0], hsv[:, :, 1].astype(np.int32), hsv[:, :, 2]
    plant = ((hh >= 25) & (hh <= 90) & (s > 50)) | ((hh >= 5) & (hh < 25) & (s > 50) & (v > 40))
    band = np.ones(plant.shape, bool)
    band[max(0, y1 - by1):max(0, y2 - by1), max(0, x1 - bx1):max(0, x2 - bx1)] = False
    return float(plant[band].mean()) if band.any() else 0.0


def _contrast_vs_ring(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                      ring: int = 16) -> float:
    """90th-percentile brightness inside the box minus the ring's median."""
    h, w = frame.shape[:2]
    bx1, by1 = max(0, x1 - ring), max(0, y1 - ring)
    bx2, by2 = min(w, x2 + ring), min(h, y2 + ring)
    v = cv2.cvtColor(frame[by1:by2, bx1:bx2], cv2.COLOR_BGR2HSV)[:, :, 2]
    inside = np.zeros(v.shape, bool)
    inside[max(0, y1 - by1):max(0, y2 - by1), max(0, x1 - bx1):max(0, x2 - bx1)] = True
    if not inside.any() or inside.all():
        return 0.0
    return float(np.percentile(v[inside], 90)) - float(np.median(v[~inside]))


def _green_ratio(crop_bgr: np.ndarray, h_min: int = 35, h_max: int = 85) -> float:
    """Fraction of pixels in HSV green range (hue h_min-h_max, sat > 50)."""
    if crop_bgr.size == 0:
        return 0.0
    hsv  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[:, :, 0] >= h_min) & (hsv[:, :, 0] <= h_max) &
        (hsv[:, :, 1].astype(np.int32) > 50)
    )
    return float(np.sum(mask)) / max(1, mask.size)


def _pale_ratio(crop_bgr: np.ndarray) -> float:
    """Fraction of bright, low-saturation (white insect) pixels."""
    if crop_bgr.size == 0:
        return 0.0
    hsv  = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] >= config.WF_PALE_V_MIN) &
            (hsv[:, :, 1] <= config.WF_PALE_S_MAX))
    return float(np.sum(mask)) / max(1, mask.size)


def _covers_worm(box: Tuple[int, ...], worm_boxes: List[Tuple[int, ...]]) -> bool:
    """True if enough of any worm box lies inside this (fungus) box."""
    bx1, by1, bx2, by2 = box
    for wx1, wy1, wx2, wy2 in worm_boxes:
        inter = (max(0, min(bx2, wx2) - max(bx1, wx1)) *
                 max(0, min(by2, wy2) - max(by1, wy1)))
        worm_area = max(1, (wx2 - wx1) * (wy2 - wy1))
        if inter / worm_area >= config.FUNGUS_WORM_OVERLAP:
            return True
    return False


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

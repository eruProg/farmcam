"""
snapshot_writer.py — Background thread for saving annotated snapshots
======================================================================
The inference thread submits frames via submit(); actual file I/O runs
in a daemon thread so detection latency is never affected by disk writes.

Writes:
  - Full annotated frame:  data/snapshots/<track_id>/<unix_ts>_full.jpg
  - Thumbnail (200×150):   data/snapshots/<track_id>/<unix_ts>_thumb.jpg

Pruning (runs hourly):
  - Deletes oldest snapshots (by mtime) until under RETENTION_MAX_GB
  - Deletes snapshots older than RETENTION_MAX_DAYS days
  - Never deletes snapshots belonging to 'active' tracks (checks DB)

Usage:
    from snapshot_writer import SnapshotWriter
    sw = SnapshotWriter(db)
    sw.start()
    sw.submit(frame_bgr, confirmed_dets, track_id, frame_meta)
    sw.stop()
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

import cv2
import numpy as np

import config

log = logging.getLogger("farmcam.snapshots")

_THUMB_SIZE = (200, 150)


class SnapshotWriter:

    def __init__(self, db=None, clock_fn=None) -> None:
        self._db          = db            # PersistenceDB (optional, for prune guard)
        self._clock       = clock_fn or time.time
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: Optional[threading.Thread] = None
        self._prune_thread: Optional[threading.Thread] = None
        self._running     = False
        config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._worker, daemon=True, name="snapshot_writer"
        )
        self._prune_thread = threading.Thread(
            target=self._prune_loop, daemon=True, name="snapshot_pruner"
        )
        self._thread.start()
        self._prune_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def submit(
        self,
        frame_bgr:  np.ndarray,
        dets:       List,
        track_id:   str,
        frame_meta: Any,
    ) -> None:
        """
        Non-blocking.  Drops the frame silently if the queue is full
        (detection loop must not be delayed by disk I/O).
        """
        try:
            self._queue.put_nowait({
                "frame":      frame_bgr,
                "dets":       dets,
                "track_id":   track_id,
                "frame_meta": frame_meta,
                "ts":         self._clock(),
            })
        except queue.Full:
            log.debug("snapshot queue full — frame dropped for track %s", track_id[:8])

    # ── Worker ────────────────────────────────────────────────────────────

    def _worker(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._write_snapshot(item)
            except Exception as exc:
                log.error("snapshot write error: %s", exc)

    def _write_snapshot(self, item: dict) -> None:
        frame      = item["frame"]
        dets       = item["dets"]
        track_id   = item["track_id"]
        meta       = item["frame_meta"]
        ts         = int(item["ts"])

        dest_dir = config.SNAPSHOT_DIR / track_id[:8]
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Annotate the full frame
        annotated = frame.copy()
        for det in dets:
            try:
                x1, y1, x2, y2 = int(det[0]), int(det[1]), int(det[2]), int(det[3])
                cls  = str(det[4])
                conf = float(det[5])
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 220, 80), 2)
                label = f"{cls} {conf:.2f}"
                cv2.putText(annotated, label, (max(0, x1), max(12, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 80), 1)
            except Exception:
                pass

        # Station / position watermark
        wm = (f"S{meta.station} {meta.position}  "
              f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['ts']))}")
        cv2.putText(annotated, wm, (8, annotated.shape[0] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        full_path  = dest_dir / f"{ts}_full.jpg"
        thumb_path = dest_dir / f"{ts}_thumb.jpg"

        # Atomic write: write to tmp then rename
        self._atomic_write(annotated, full_path, quality=85)
        thumb = cv2.resize(annotated, _THUMB_SIZE, interpolation=cv2.INTER_AREA)
        self._atomic_write(thumb, thumb_path, quality=75)

        # Update DB if available
        if self._db:
            try:
                self._db.update_track_snapshot(
                    track_id,
                    str(full_path),
                    str(thumb_path),
                )
            except Exception as exc:
                log.debug("DB snapshot update failed: %s", exc)

        log.debug("snapshot saved track=%s full=%s", track_id[:8], full_path.name)

    @staticmethod
    def _atomic_write(img: np.ndarray, path: Path, quality: int = 85) -> None:
        tmp = path.with_suffix(".tmp.jpg")
        cv2.imwrite(str(tmp), img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        os.replace(str(tmp), str(path))   # atomic on POSIX

    # ── Pruner ────────────────────────────────────────────────────────────

    def _prune_loop(self) -> None:
        """Run every hour."""
        while self._running:
            time.sleep(3600)
            try:
                self._prune()
            except Exception as exc:
                log.error("snapshot prune error: %s", exc)

    def _prune(self) -> None:
        cutoff_age  = time.time() - config.RETENTION_MAX_DAYS * 86400
        max_bytes   = int(config.RETENTION_MAX_GB * 1024 ** 3)

        # Get active track IDs to protect their snapshots
        protected_prefixes: set = set()
        if self._db:
            try:
                rows = self._db.get_tracks_page(status="active", page_size=9999)
                for row in rows["items"]:
                    protected_prefixes.add(row["id"][:8])
            except Exception:
                pass

        # Collect all snapshot files sorted oldest-first
        all_files: list = []
        for jpg in config.SNAPSHOT_DIR.rglob("*.jpg"):
            if "_thumb" in jpg.name:
                continue
            folder = jpg.parent.name
            if folder in protected_prefixes:
                continue
            all_files.append((jpg.stat().st_mtime, jpg))
        all_files.sort()

        # Delete by age
        for mtime, jpg in all_files:
            if mtime < cutoff_age:
                _safe_remove(jpg)
                _safe_remove(jpg.with_name(jpg.stem + "_thumb.jpg"))

        # Delete by size
        total = _dir_size(config.SNAPSHOT_DIR)
        for _, jpg in all_files:
            if total <= max_bytes:
                break
            sz = jpg.stat().st_size if jpg.exists() else 0
            if _safe_remove(jpg):
                _safe_remove(jpg.with_name(jpg.stem + "_thumb.jpg"))
                total -= sz

        log.info("snapshot prune done: %.1f MB remain",
                 _dir_size(config.SNAPSHOT_DIR) / 1024 ** 2)


def _safe_remove(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except Exception:
        return False


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

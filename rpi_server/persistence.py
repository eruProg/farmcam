"""
persistence.py — SQLite WAL persistence layer
=============================================
Provides:
  - PersistenceDB  : full detection lifecycle (scans, tracks, snapshots)
  - CooldownManager: persisted pump/SMS cooldowns that survive reboots

Schema design decisions:
  - WAL mode for concurrent reader + writer (detect_loop writes, HTTP reads)
  - tracks are NEVER deleted; false-positive verdict sets is_fp=1
  - rescan merge is ATOMIC: one transaction touches existing + new tracks
  - aborted scans do not touch tracks at all

Usage:
    from persistence import PersistenceDB, CooldownManager
    import config

    db = PersistenceDB(config.DB_PATH)
    cm = CooldownManager(db)

    # In detect_loop start:
    scan_id = db.begin_scan(station, position)

    # On each confirmed detection:
    track_id = db.upsert_track(scan_id, station, position, det)

    # On clean scan completion:
    db.commit_scan(scan_id, station, position, confirmed_tracks)

    # On abort (transit started early, station changed, etc.):
    db.abort_scan(scan_id)

    # For /api/lettuce endpoint:
    rows = db.get_station_summary()
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config

log = logging.getLogger("farmcam.persistence")

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS scans (
    id           TEXT PRIMARY KEY,
    station      INTEGER NOT NULL,
    position     TEXT    NOT NULL,      -- "upper" | "lower"
    started_at   REAL    NOT NULL,
    completed_at REAL,
    status       TEXT    NOT NULL DEFAULT 'in_progress'
                                   -- in_progress | completed | aborted
);

CREATE TABLE IF NOT EXISTS tracks (
    id                   TEXT PRIMARY KEY,
    scan_id              TEXT REFERENCES scans(id),
    station              INTEGER NOT NULL,
    position             TEXT    NOT NULL,
    class                TEXT    NOT NULL,
    confidence           REAL    NOT NULL,
    first_seen           REAL    NOT NULL,
    last_seen            REAL    NOT NULL,
    scans_seen           INTEGER NOT NULL DEFAULT 1,
    status               TEXT    NOT NULL DEFAULT 'active',  -- active | resolved
    resolved_at          REAL,
    resolved_by_scan_id  TEXT,
    bbox                 TEXT,           -- JSON [x1,y1,x2,y2]
    snapshot_path        TEXT,
    thumb_path           TEXT,
    is_fp                INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          TEXT PRIMARY KEY,
    track_id    TEXT REFERENCES tracks(id),
    path        TEXT NOT NULL,
    thumb_path  TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cooldowns (
    key        TEXT PRIMARY KEY,
    last_fired REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tracks_station
    ON tracks(station, position, status);
CREATE INDEX IF NOT EXISTS idx_tracks_first_seen
    ON tracks(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_track
    ON snapshots(track_id);
"""

import json as _json


class PersistenceDB:
    """
    Thread-safe SQLite wrapper.  All methods acquire the internal lock
    and open a new connection per call (WAL allows concurrent reads).
    For write-heavy callers, re-use the same connection inside a
    context manager (with db.connection() as conn: ...).
    """

    def __init__(self, db_path: str = None) -> None:
        self._path = db_path or config.DB_PATH
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Schema initialisation ─────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── Connection helper ─────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Scan lifecycle ────────────────────────────────────────────────────

    def begin_scan(self, station: int, position: str) -> str:
        """Create a new scan row and return its scan_id (UUID)."""
        scan_id = str(uuid.uuid4())
        now     = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scans(id,station,position,started_at,status) "
                "VALUES(?,?,?,?,'in_progress')",
                (scan_id, station, position, now)
            )
        log.debug("begin_scan %s station=%d pos=%s", scan_id[:8], station, position)
        return scan_id

    def abort_scan(self, scan_id: str) -> None:
        """Mark scan as aborted.  Does NOT touch tracks."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE scans SET status='aborted', completed_at=? WHERE id=?",
                (time.time(), scan_id)
            )
        log.debug("abort_scan %s", scan_id[:8])

    def commit_scan(
        self,
        scan_id:         str,
        station:         int,
        position:        str,
        confirmed_dets:  List[List],   # each: [x1,y1,x2,y2,class,conf,...]
    ) -> List[str]:
        """
        Atomic rescan merge.  Returns list of new track_ids that were
        INSERTED (not merged) — these should trigger actuators.

        Algorithm:
        1. Find all current active tracks for (station, position).
        2. For each confirmed_det:
            - If IoU >= TRACK_IOU_RESCAN with an existing active track
              of the same class → UPDATE scans_seen, confidence, last_seen.
            - Else → INSERT as a new active track.
        3. Existing active tracks not matched by any confirmed_det →
              status=resolved.
        4. All in one transaction.
        """
        now         = time.time()
        new_ids: List[str] = []

        with self._conn() as conn:
            # Load existing active tracks
            rows = conn.execute(
                "SELECT id, class, bbox FROM tracks "
                "WHERE station=? AND position=? AND status='active'",
                (station, position)
            ).fetchall()

            existing = [
                {
                    "id":   row["id"],
                    "cls":  row["class"],
                    "bbox": _json.loads(row["bbox"] or "[0,0,0,0]"),
                }
                for row in rows
            ]

            matched_existing = set()

            for det in confirmed_dets:
                x1, y1, x2, y2 = int(det[0]), int(det[1]), int(det[2]), int(det[3])
                cls  = str(det[4])
                conf = float(det[5])
                bbox = [x1, y1, x2, y2]

                best_ex   = None
                best_iou  = 0.0
                for ex in existing:
                    if ex["cls"] != cls:
                        continue
                    iou = _iou(bbox, ex["bbox"])
                    if iou >= config.TRACK_IOU_RESCAN and iou > best_iou:
                        best_iou = iou
                        best_ex  = ex

                if best_ex:
                    matched_existing.add(best_ex["id"])
                    conn.execute(
                        "UPDATE tracks SET scans_seen=scans_seen+1, "
                        "confidence=?, last_seen=?, scan_id=?, bbox=? WHERE id=?",
                        (conf, now, scan_id, _json.dumps(bbox), best_ex["id"])
                    )
                else:
                    track_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO tracks(id,scan_id,station,position,class,"
                        "confidence,first_seen,last_seen,scans_seen,bbox,status) "
                        "VALUES(?,?,?,?,?,?,?,?,1,?,'active')",
                        (track_id, scan_id, station, position, cls,
                         conf, now, now, _json.dumps(bbox))
                    )
                    new_ids.append(track_id)

            # Resolve unmatched existing tracks
            for ex in existing:
                if ex["id"] not in matched_existing:
                    conn.execute(
                        "UPDATE tracks SET status='resolved', resolved_at=?, "
                        "resolved_by_scan_id=? WHERE id=?",
                        (now, scan_id, ex["id"])
                    )

            # Mark scan completed
            conn.execute(
                "UPDATE scans SET status='completed', completed_at=? WHERE id=?",
                (now, scan_id)
            )

        log.debug(
            "commit_scan %s station=%d pos=%s new=%d resolved=%d",
            scan_id[:8], station, position, len(new_ids),
            sum(1 for ex in existing if ex["id"] not in matched_existing),
        )
        return new_ids

    # ── Track queries ─────────────────────────────────────────────────────

    def get_station_summary(self) -> List[Dict[str, Any]]:
        """
        Returns one row per station with active track counts and last scan time.
        Used by /api/lettuce.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    t.station,
                    t.class,
                    COUNT(*) AS count,
                    MAX(t.confidence) AS max_conf,
                    MAX(t.last_seen)  AS last_seen,
                    MAX(t.thumb_path) AS thumb_path
                FROM tracks t
                WHERE t.status='active' AND t.is_fp=0
                GROUP BY t.station, t.class
                ORDER BY t.station, t.class
            """).fetchall()

            last_scan = conn.execute("""
                SELECT station, MAX(completed_at) AS completed_at
                FROM scans WHERE status='completed'
                GROUP BY station
            """).fetchall()

        by_station: Dict[int, Dict] = {}
        for i in range(1, 5):
            by_station[i] = {
                "station_id":     i,
                "detections":     [],
                "last_scanned":   None,
                "active_threats": 0,
                "severity":       "clear",
            }

        for row in rows:
            s = row["station"]
            by_station.setdefault(s, {
                "station_id": s, "detections": [], "last_scanned": None,
                "active_threats": 0, "severity": "clear",
            })
            by_station[s]["detections"].append({
                "class":      row["class"],
                "count":      row["count"],
                "confidence": round(row["max_conf"], 3),
                "thumb_path": row["thumb_path"],
            })
            by_station[s]["active_threats"] += row["count"]

        for row in last_scan:
            by_station[row["station"]]["last_scanned"] = row["completed_at"]

        # Compute per-station severity
        for s, data in by_station.items():
            pests  = sum(d["count"] for d in data["detections"]
                         if d["class"] in ("whitefly","whiteflies","worm"))
            fungus = sum(d["count"] for d in data["detections"]
                         if d["class"] == "fungus")
            if pests >= config.SEV_PEST_HIGH or fungus >= 2:
                data["severity"] = "high"
            elif pests >= config.SEV_PEST_MEDIUM or fungus >= 1:
                data["severity"] = "medium"
            else:
                data["severity"] = "clear"

        return list(by_station.values())

    def get_tracks_page(
        self,
        station:   Optional[int]  = None,
        position:  Optional[str]  = None,
        cls:       Optional[str]  = None,
        status:    str            = "active",
        is_fp:     Optional[int]  = None,
        date_from: Optional[float] = None,
        date_to:   Optional[float] = None,
        page:      int            = 1,
        page_size: int            = 20,
    ) -> Dict[str, Any]:
        """Paginated track list for /api/logs."""
        where_clauses = []
        params: List[Any] = []

        if station  is not None: where_clauses.append("station=?");  params.append(station)
        if position is not None: where_clauses.append("position=?"); params.append(position)
        if cls      is not None: where_clauses.append("class=?");    params.append(cls)
        if status   is not None: where_clauses.append("status=?");   params.append(status)
        if is_fp    is not None: where_clauses.append("is_fp=?");    params.append(is_fp)
        if date_from is not None:
            where_clauses.append("first_seen>=?"); params.append(date_from)
        if date_to  is not None:
            where_clauses.append("first_seen<=?"); params.append(date_to)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        offset = (page - 1) * page_size

        with self._conn() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) FROM tracks {where}", params
            ).fetchone()
            total = total_row[0] if total_row else 0

            rows = conn.execute(
                f"SELECT * FROM tracks {where} "
                f"ORDER BY first_seen DESC LIMIT ? OFFSET ?",
                params + [page_size, offset]
            ).fetchall()

        return {
            "items":     [dict(r) for r in rows],
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     max(1, -(-total // page_size)),
        }

    def get_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id=?", (track_id,)
            ).fetchone()
            return dict(row) if row else None

    def mark_false_positive(self, track_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE tracks SET is_fp=1, status='resolved', resolved_at=? WHERE id=?",
                (time.time(), track_id)
            )
        return cur.rowcount > 0

    def precision_by_class(self) -> Dict[str, Optional[float]]:
        """TP/(TP+FP) per class from false-positive verdicts."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT class, is_fp, COUNT(*) as n FROM tracks "
                "WHERE status IN ('active','resolved') "
                "GROUP BY class, is_fp"
            ).fetchall()
        totals: Dict[str, List[int]] = {}
        for r in rows:
            cls  = r["class"]
            if cls not in totals:
                totals[cls] = [0, 0]   # [tp, fp]
            if r["is_fp"]:
                totals[cls][1] += r["n"]
            else:
                totals[cls][0] += r["n"]
        result: Dict[str, Optional[float]] = {}
        for cls, (tp, fp) in totals.items():
            result[cls] = round(tp / max(tp + fp, 1), 4) if (tp + fp) > 0 else None
        return result

    def update_track_snapshot(
        self, track_id: str, snapshot_path: str, thumb_path: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tracks SET snapshot_path=?, thumb_path=? WHERE id=?",
                (snapshot_path, thumb_path, track_id)
            )


# ── Cooldown manager ──────────────────────────────────────────────────────────

class CooldownManager:
    """
    Persisted pump/SMS cooldowns.  Uses the cooldowns table in the DB so
    the cooldown survives a server restart.
    """

    def __init__(self, db: PersistenceDB) -> None:
        self._db = db

    def can_fire(self, key: str, cooldown_s: float, now: float = None) -> bool:
        now = now or time.time()
        last = self._last_fired(key)
        return (now - last) >= cooldown_s

    def record_fire(self, key: str, now: float = None) -> None:
        now = now or time.time()
        with self._db._conn() as conn:
            conn.execute(
                "INSERT INTO cooldowns(key,last_fired) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET last_fired=excluded.last_fired",
                (key, now)
            )

    def status(self, key: str, cooldown_s: float, now: float = None) -> Dict[str, Any]:
        now  = now or time.time()
        last = self._last_fired(key)
        elapsed   = now - last
        remaining = max(0.0, cooldown_s - elapsed)
        return {
            "active":            remaining > 0,
            "remaining_s":       round(remaining, 1),
            "total_s":           cooldown_s,
            "last_triggered_at": last if last > 0 else None,
        }

    def _last_fired(self, key: str) -> float:
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT last_fired FROM cooldowns WHERE key=?", (key,)
            ).fetchone()
        return row["last_fired"] if row else 0.0


# ── IoU helper ────────────────────────────────────────────────────────────────

def _iou(a: List[int], b: List[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / max(union, 1)

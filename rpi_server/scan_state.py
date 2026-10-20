"""Alternating four-station scan schedule with immutable per-run timings.
The motor is controlled separately; only an explicit start begins scanning.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional

import config
from scan_settings import ScanSettings


# ── Phase enum ───────────────────────────────────────────────────────────────

class ScanPhase(Enum):
    INITIAL_DELAY     = auto()
    RETURNING         = auto()
    IDLE              = auto()
    MOVING_TO_STATION = auto()
    SETTLING          = auto()
    DETECT_UPPER      = auto()
    TRANSIT_TO_LOWER  = auto()
    TRANSIT_TO_UPPER  = auto()
    DETECT_LOWER      = auto()

    # Human-readable labels for the UI
    def label(self) -> str:
        return {
            ScanPhase.INITIAL_DELAY: "Starting scan",
            ScanPhase.RETURNING: "Returning to lettuce 1",
            ScanPhase.IDLE: "Ready to start",
            ScanPhase.MOVING_TO_STATION: "Moving",
            ScanPhase.SETTLING:          "Settling",
            ScanPhase.DETECT_UPPER:      "Detecting (Upper)",
            ScanPhase.TRANSIT_TO_LOWER:  "Lowering",
            ScanPhase.TRANSIT_TO_UPPER:  "Raising",
            ScanPhase.DETECT_LOWER:      "Detecting (Lower)",
        }[self]

    def position(self) -> str:
        """Camera position during this phase."""
        return "lower" if self is ScanPhase.DETECT_LOWER else "upper"


# ── Frame metadata ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FrameMeta:
    """Immutable stamp attached to each captured frame.

    Every downstream consumer (verifier, tracker, persistence) must read
    attribution from this struct, never from bare globals.
    """
    seq:         int
    captured_at: float     # time.monotonic() at capture
    scan_id:     str       # UUID changes when a new detection window begins
    station:     int       # 1-4
    position:    str       # "upper" | "lower"
    phase:       ScanPhase


# ── State machine ─────────────────────────────────────────────────────────────

class ScanStateMachine:
    """
    Drives the per-station scan cycle.  Call tick() from your detect_loop
    to advance transitions.  All timing comes from config.py so there are
    no magic numbers here.

    The on_phase_change callbacks fire synchronously inside tick(); keep them
    fast (e.g. flush a queue, log a timestamp).
    """

    def __init__(
        self,
        stations:  List[int]          = None,
        time_fn:   Callable[[], float] = None,
    ) -> None:
        self._stations    = list(stations or config.LETTUCE_SEQUENCE)
        self._time_fn     = time_fn or time.monotonic

        self.settings = ScanSettings()
        self.run_id = None
        self._position = "lower"
        self._station_idx: int      = 0
        self._phase:       ScanPhase = ScanPhase.IDLE
        self._phase_started:  float  = 0.0
        self._phase_duration: float  = 0.0
        self._scan_id:        str    = ""
        self._move_seconds = config.MOVE_SECS
        # The next phase to enter after a SETTLING period
        self._next_after_settle: ScanPhase = ScanPhase.DETECT_UPPER

        self._callbacks: List[Callable[[ScanPhase, int], None]] = []

    # ── Public start / stop ───────────────────────────────────────────────

    def start(self, settings=None) -> None:
        if self._phase is not ScanPhase.IDLE:
            raise RuntimeError('A scan is already running')
        self.settings = ScanSettings.parse((settings or self.settings).to_dict())
        self._station_idx = 0
        self._position = 'lower'
        self.run_id = str(uuid.uuid4())
        self._begin_phase(ScanPhase.INITIAL_DELAY if self.settings.initial_delay_s else ScanPhase.DETECT_LOWER)

    def stop(self) -> None:
        self._begin_phase(ScanPhase.IDLE)

    def tick(self) -> Optional[ScanPhase]:
        # Anchor transitions to the planned deadline, not the inference-loop
        # wake-up time, so processing latency does not accumulate motor drift.
        self._scheduled_start = self._phase_started + self.phase_duration
        try:
            return self._tick_due()
        finally:
            self._scheduled_start = None

    def _tick_due(self) -> Optional[ScanPhase]:
        p = self._phase
        if p is ScanPhase.IDLE or self._time_fn() < self._phase_started + self.phase_duration:
            return None
        if p is ScanPhase.INITIAL_DELAY:
            return self._begin_phase(ScanPhase.DETECT_LOWER)
        if p in (ScanPhase.MOVING_TO_STATION, ScanPhase.RETURNING):
            return self._begin_phase(ScanPhase.DETECT_LOWER if self.station % 2 else ScanPhase.DETECT_UPPER)
        if p is ScanPhase.TRANSIT_TO_UPPER:
            return self._begin_phase(ScanPhase.DETECT_UPPER)
        if p is ScanPhase.TRANSIT_TO_LOWER:
            return self._begin_phase(ScanPhase.DETECT_LOWER)
        first = ScanPhase.DETECT_LOWER if self.station % 2 else ScanPhase.DETECT_UPPER
        if p is first:
            return self._begin_phase(ScanPhase.TRANSIT_TO_UPPER if self.station % 2 else ScanPhase.TRANSIT_TO_LOWER)
        old = self.station
        self._move_seconds = (self.settings.return_s if old == 4 else
                              getattr(self.settings, f'travel_{old}_{old+1}_s'))
        self._advance_station()
        return self._begin_phase(ScanPhase.RETURNING if old == 4 else ScanPhase.MOVING_TO_STATION)

    # ── Properties for callers ────────────────────────────────────────────

    @property
    def phase(self) -> ScanPhase:
        return self._phase

    @property
    def station(self) -> int:
        return self._stations[self._station_idx]

    @property
    def scan_id(self) -> str:
        return self._scan_id

    @property
    def is_detecting(self) -> bool:
        return self._phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER)

    @property
    def position(self) -> str:
        return self._position

    @property
    def phase_started_at(self) -> float:
        return self._phase_started

    @property
    def phase_duration(self) -> float:
        return self._phase_duration

    @property
    def elapsed(self) -> float:
        return self._time_fn() - self._phase_started

    @property
    def remaining(self) -> float:
        return max(0.0, self._phase_duration - self.elapsed)

    @property
    def settle_elapsed(self) -> float:
        """Seconds since the current phase started.  Used for SETTLE_SECS gate."""
        if self._phase is not ScanPhase.SETTLING:
            return float("inf")
        return self._time_fn() - self._phase_started

    @property
    def progress(self) -> float:
        """0.0 – 1.0 progress through the current phase (for progress bars)."""
        if self._phase_duration <= 0:
            return 1.0
        return min(1.0, self.elapsed / self._phase_duration)

    def state_dict(self) -> dict:
        """Serialisable snapshot for /api/system and Flutter countdown sync."""
        return {
            "run_id": self.run_id,
            "running": self.phase is not ScanPhase.IDLE,
            "settings": self.settings.to_dict(),
            "phase":            self._phase.name,
            "phase_label":      self._phase.label(),
            "position":         self.position,
            "station":          self.station,
            "scan_id":          self._scan_id,
            "phase_started_at": self._phase_started,
            "phase_duration_s": self._phase_duration,
            "remaining_s":      round(self.remaining, 1),
            "progress":         round(self.progress, 3),
            "is_detecting":     self.is_detecting,
            "server_time":      self._time_fn(),
        }

    # ── Frame stamping ────────────────────────────────────────────────────

    def make_frame_meta(self, seq: int, captured_at=None) -> FrameMeta:
        """Create an immutable stamp for a frame captured RIGHT NOW."""
        return FrameMeta(
            seq         = seq,
            captured_at = self._time_fn() if captured_at is None else captured_at,
            scan_id     = self._scan_id,
            station     = self.station,
            position    = self.position,
            phase       = self._phase,
        )

    # ── Phase-change callbacks ────────────────────────────────────────────

    def on_phase_change(
        self, cb: Callable[[ScanPhase, int], None]
    ) -> None:
        """Register a callback invoked synchronously on every phase transition.
        Signature: cb(new_phase: ScanPhase, station: int)
        """
        self._callbacks.append(cb)

    # ── Internals ─────────────────────────────────────────────────────────

    def _begin_phase(
        self,
        phase:      ScanPhase,
        next_after: ScanPhase = ScanPhase.DETECT_UPPER,
    ) -> ScanPhase:
        if phase is ScanPhase.DETECT_UPPER:
            self._position = "upper"
        elif phase is ScanPhase.DETECT_LOWER:
            self._position = "lower"
        self._phase          = phase
        scheduled = getattr(self, '_scheduled_start', None)
        self._phase_started = self._time_fn() if scheduled is None else scheduled
        self._phase_duration = self._phase_to_duration(phase)
        if phase in (ScanPhase.MOVING_TO_STATION, ScanPhase.RETURNING):
            self._phase_duration = self._move_seconds
        if phase is ScanPhase.SETTLING:
            self._next_after_settle = next_after
        # New scan_id whenever a detection window starts or we start moving
        if phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER,
                     ScanPhase.MOVING_TO_STATION):
            self._scan_id = str(uuid.uuid4())
        for cb in self._callbacks:
            try:
                cb(phase, self.station)
            except Exception:
                pass
        return phase

    def _advance_station(self) -> None:
        self._station_idx = (self._station_idx + 1) % len(self._stations)

    def _move_duration(self) -> float:
        """After station 4 the loop-back path is longer."""
        return self._move_seconds

    def _phase_to_duration(self, phase):
        return {
            ScanPhase.INITIAL_DELAY: self.settings.initial_delay_s,
            ScanPhase.DETECT_UPPER: self.settings.scan_s,
            ScanPhase.DETECT_LOWER: self.settings.scan_s,
            ScanPhase.TRANSIT_TO_UPPER: self.settings.raise_s,
            ScanPhase.TRANSIT_TO_LOWER: self.settings.lower_s,
        }.get(phase, 0.0)

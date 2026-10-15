"""
scan_state.py — Explicit scan state machine for FarmCam
=========================================================
Replaces the bare globals (current_state / active_station_id) in main.py.

State sequence per station:
    DETECT_UPPER → TRANSIT_TO_LOWER → SETTLING → DETECT_LOWER
    → TRANSIT_TO_UPPER → MOVING_TO_STATION → SETTLING → DETECT_UPPER (next)

All timing values are read from config.py.
The clock is injectable (time_fn) so tests can run with a FakeClock.

Usage in main.py (coordinator section only):
    from scan_state import ScanStateMachine, ScanPhase, FrameMeta
    sm = ScanStateMachine()
    sm.start()

    # In detect_loop tick:
    sm.tick()          # drive transitions
    if not sm.is_detecting:
        continue       # skip inference during transit / moving / settling

    meta = sm.make_frame_meta(seq)   # stamp every accepted frame
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional

import config


# ── Phase enum ───────────────────────────────────────────────────────────────

class ScanPhase(Enum):
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
            ScanPhase.IDLE:              "Idle",
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

        self._station_idx: int      = 0
        self._phase:       ScanPhase = ScanPhase.IDLE
        self._phase_started:  float  = 0.0
        self._phase_duration: float  = 0.0
        self._scan_id:        str    = ""
        # The next phase to enter after a SETTLING period
        self._next_after_settle: ScanPhase = ScanPhase.DETECT_UPPER

        self._callbacks: List[Callable[[ScanPhase, int], None]] = []

    # ── Public start / stop ───────────────────────────────────────────────

    def start(self) -> None:
        """Begin the first detection window immediately."""
        self._begin_phase(ScanPhase.DETECT_UPPER)

    def stop(self) -> None:
        self._phase = ScanPhase.IDLE

    # ── Main tick (call from detect_loop every iteration) ─────────────────

    def tick(self) -> Optional[ScanPhase]:
        """Advance the state machine.  Returns the new phase if a transition
        happened, None otherwise."""
        now     = self._time_fn()
        elapsed = now - self._phase_started
        p       = self._phase

        if p is ScanPhase.IDLE:
            return None

        if p is ScanPhase.SETTLING:
            if elapsed >= config.SETTLE_SECS:
                return self._begin_phase(self._next_after_settle)
            return None

        if p is ScanPhase.DETECT_UPPER:
            if elapsed >= config.DETECT_WINDOW_SECS:
                return self._begin_phase(ScanPhase.TRANSIT_TO_LOWER)
            return None

        if p is ScanPhase.TRANSIT_TO_LOWER:
            if elapsed >= config.TRANSIT_DOWN_SECS:
                return self._begin_phase(ScanPhase.SETTLING,
                                         next_after=ScanPhase.DETECT_LOWER)
            return None

        if p is ScanPhase.DETECT_LOWER:
            if elapsed >= config.DETECT_WINDOW_SECS:
                return self._begin_phase(ScanPhase.TRANSIT_TO_UPPER)
            return None

        if p is ScanPhase.TRANSIT_TO_UPPER:
            if elapsed >= config.TRANSIT_UP_SECS:
                self._advance_station()
                return self._begin_phase(ScanPhase.MOVING_TO_STATION)
            return None

        if p is ScanPhase.MOVING_TO_STATION:
            if elapsed >= self._move_duration():
                return self._begin_phase(ScanPhase.SETTLING,
                                         next_after=ScanPhase.DETECT_UPPER)
            return None

        return None

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
        return self._phase.position()

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

    def make_frame_meta(self, seq: int) -> FrameMeta:
        """Create an immutable stamp for a frame captured RIGHT NOW."""
        return FrameMeta(
            seq         = seq,
            captured_at = self._time_fn(),
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
        self._phase          = phase
        self._phase_started  = self._time_fn()
        self._phase_duration = self._phase_to_duration(phase)
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
        if self._stations[self._station_idx] == 4:
            return config.LOOP_AFTER_4_SECS
        return config.MOVE_SECS

    @staticmethod
    def _phase_to_duration(phase: ScanPhase) -> float:
        return {
            ScanPhase.DETECT_UPPER:      config.DETECT_WINDOW_SECS,
            ScanPhase.TRANSIT_TO_LOWER:  config.TRANSIT_DOWN_SECS,
            ScanPhase.SETTLING:          config.SETTLE_SECS,
            ScanPhase.DETECT_LOWER:      config.DETECT_WINDOW_SECS,
            ScanPhase.TRANSIT_TO_UPPER:  config.TRANSIT_UP_SECS,
            ScanPhase.MOVING_TO_STATION: config.MOVE_SECS,  # overridden for station 4
            ScanPhase.IDLE:              0.0,
        }.get(phase, 0.0)

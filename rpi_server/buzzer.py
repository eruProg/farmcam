"""
buzzer.py — Non-blocking PWM buzzer scheduler
=============================================
All beeping is edge-triggered (new track only) and runs in a daemon thread
so the main detect_loop never blocks.

Features:
  - Per-class pattern from config.BUZZ_PATTERN_*
  - Global cooldown:   BUZZ_MIN_INTERVAL_S  (any class)
  - Per-class cooldown: BUZZ_CLASS_COOLDOWN_S
  - Hard ON-time cap:  BUZZ_MAX_ON_PER_MIN_S per rolling 60-second window
  - Pattern coalescing: calls within 0.5 s of each other → one pattern
  - Mute toggle (UI controlled, no buzzer output)
  - Fail-safe: GPIO forced LOW on exception and shutdown

Usage:
    from buzzer import BuzzerScheduler
    bz = BuzzerScheduler()
    bz.start()
    bz.trigger("whitefly")   # call only for NEW confirmed tracks
    bz.mute(True)            # silence
    bz.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

import config

log = logging.getLogger("farmcam.buzzer")

_PATTERN_MAP = {
    "whitefly":   config.BUZZ_PATTERN_PEST,
    "whiteflies": config.BUZZ_PATTERN_PEST,
    "worm":       config.BUZZ_PATTERN_PEST,
    "fungus":     config.BUZZ_PATTERN_FUNGUS,
    "fault":      config.BUZZ_PATTERN_FAULT,
}

_COALESCE_WINDOW = 0.5   # seconds — calls within this window → single pattern


class BuzzerScheduler:
    """
    Thread-safe buzzer driver.  Uses RPi.GPIO PWM on BUZZER_PIN for a
    passive piezo, or falls back to simple HIGH/LOW for an active buzzer.
    """

    def __init__(self, clock_fn: Callable[[], float] = None) -> None:
        self._clock         = clock_fn or time.monotonic
        self._muted         = config.BUZZER_MUTED
        self._pending:      Optional[Tuple[str, float]] = None   # (class, queued_at)
        self._last_global:  float = 0.0
        self._class_times:  dict  = {}       # cls -> last fire time
        self._on_times:     Deque[float] = deque()  # timestamps of ON-events in last 60s
        self._total_on:     float = 0.0      # rolling total ON seconds
        self._lock          = threading.Lock()
        self._event         = threading.Event()
        self._gpio_pwm      = None
        self._thread:       Optional[threading.Thread] = None
        self._running       = False

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the player thread and initialise GPIO."""
        self._running = True
        self._setup_gpio()
        self._thread = threading.Thread(
            target=self._player_loop, daemon=True, name="buzzer"
        )
        self._thread.start()

    def stop(self) -> None:
        """Cleanly shut down and release GPIO."""
        self._running = False
        self._event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.fail_safe_off()
        self._teardown_gpio()

    def trigger(self, event_class: str) -> None:
        """
        Queue a buzz for event_class.  Call only for NEWLY confirmed tracks
        (not for retained/existing ones).  Multiple calls within the coalesce
        window produce a single pattern.
        """
        now = self._clock()
        with self._lock:
            # Coalesce: override pending only if class escalates
            if self._pending is None or (now - self._pending[1]) > _COALESCE_WINDOW:
                self._pending = (event_class, now)
            else:
                # Keep whichever class has the "louder" pattern (more beeps)
                current_len  = len(_PATTERN_MAP.get(self._pending[0], []))
                incoming_len = len(_PATTERN_MAP.get(event_class, []))
                if incoming_len > current_len:
                    self._pending = (event_class, self._pending[1])
        self._event.set()

    def mute(self, muted: bool) -> None:
        with self._lock:
            self._muted = muted
        if muted:
            self.fail_safe_off()
        log.info("buzzer muted=%s", muted)

    @property
    def is_muted(self) -> bool:
        with self._lock:
            return self._muted

    def fail_safe_off(self) -> None:
        """Force buzzer off immediately (thread-safe, can call from any thread)."""
        try:
            if self._gpio_pwm is not None:
                self._gpio_pwm.ChangeDutyCycle(0)
            else:
                import RPi.GPIO as GPIO
                GPIO.output(config.BUZZER_PIN, GPIO.LOW)
        except Exception:
            pass

    # ── Player thread ─────────────────────────────────────────────────────

    def _player_loop(self) -> None:
        while self._running:
            self._event.wait(timeout=1.0)
            self._event.clear()

            pending = None
            with self._lock:
                if self._pending:
                    pending = self._pending
                    self._pending = None

            if pending is None:
                continue

            cls, queued_at = pending
            now = self._clock()

            # Check coalesce window elapsed (give other triggers a chance to merge)
            if (now - queued_at) < _COALESCE_WINDOW:
                time.sleep(max(0.0, _COALESCE_WINDOW - (now - queued_at)))
                now = self._clock()

            with self._lock:
                if self._muted:
                    continue
                # Global cooldown
                if (now - self._last_global) < config.BUZZ_MIN_INTERVAL_S:
                    continue
                # Per-class cooldown
                if (now - self._class_times.get(cls, 0.0)) < config.BUZZ_CLASS_COOLDOWN_S:
                    continue
                # Rolling ON-time cap
                self._purge_on_times(now)
                if self._total_on >= config.BUZZ_MAX_ON_PER_MIN_S:
                    log.warning("buzzer ON-time cap reached (%.1f s / 60 s)", self._total_on)
                    continue

                self._last_global = now
                self._class_times[cls] = now

            pattern = _PATTERN_MAP.get(cls, config.BUZZ_PATTERN_FAULT)
            self._play_pattern(pattern, now)

    def _play_pattern(self, pattern: list, started_at: float) -> None:
        """Play a pattern synchronously in the player thread."""
        total_on = sum(on for on, _ in pattern) / 1000.0
        for on_ms, off_ms in pattern:
            if not self._running:
                break
            self._buzz_on()
            time.sleep(on_ms / 1000.0)
            self._buzz_off()
            if off_ms > 0:
                time.sleep(off_ms / 1000.0)
        # Record ON time
        with self._lock:
            self._on_times.append(started_at)
            self._total_on += total_on

    def _purge_on_times(self, now: float) -> None:
        """Remove events older than 60 s and recompute total_on."""
        cutoff = now - 60.0
        while self._on_times and self._on_times[0] < cutoff:
            self._on_times.popleft()
        # Approximate: each pattern = BUZZ_MAX_ON_PER_MIN_S / len contribution
        # Re-sum from recorded events (stored as count * avg_on)
        # For simplicity: reset total_on from remaining deque length × avg pattern ON
        avg_on = sum(
            sum(on for on, _ in p) / 1000.0
            for p in _PATTERN_MAP.values()
        ) / max(len(_PATTERN_MAP), 1)
        self._total_on = len(self._on_times) * avg_on

    # ── GPIO ──────────────────────────────────────────────────────────────

    def _setup_gpio(self) -> None:
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(config.BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
            try:
                self._gpio_pwm = GPIO.PWM(config.BUZZER_PIN, config.BUZZER_FREQ_HZ)
                self._gpio_pwm.start(0)
                log.info("buzzer: PWM mode on GPIO %d @ %d Hz",
                         config.BUZZER_PIN, config.BUZZER_FREQ_HZ)
            except Exception:
                self._gpio_pwm = None
                log.info("buzzer: digital mode on GPIO %d (no PWM)", config.BUZZER_PIN)
        except ImportError:
            log.warning("RPi.GPIO not available — buzzer disabled (dev mode)")

    def _teardown_gpio(self) -> None:
        try:
            if self._gpio_pwm is not None:
                self._gpio_pwm.stop()
            import RPi.GPIO as GPIO
            GPIO.cleanup(config.BUZZER_PIN)
        except Exception:
            pass

    def _buzz_on(self) -> None:
        try:
            if self._gpio_pwm is not None:
                self._gpio_pwm.ChangeDutyCycle(config.BUZZER_DUTY)
            else:
                import RPi.GPIO as GPIO
                GPIO.output(config.BUZZER_PIN, GPIO.HIGH)
        except Exception as exc:
            log.debug("buzz_on error: %s", exc)

    def _buzz_off(self) -> None:
        try:
            if self._gpio_pwm is not None:
                self._gpio_pwm.ChangeDutyCycle(0)
            else:
                import RPi.GPIO as GPIO
                GPIO.output(config.BUZZER_PIN, GPIO.LOW)
        except Exception as exc:
            log.debug("buzz_off error: %s", exc)

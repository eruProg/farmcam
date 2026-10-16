"""Coalesced ESP32 GPIO 26 buzzer with exact rolling duty accounting."""
import threading
import time
from collections import deque
import config

_PATTERN_MAP = {'whitefly': config.BUZZ_PATTERN_PEST, 'whiteflies': config.BUZZ_PATTERN_PEST,
                'worm': config.BUZZ_PATTERN_PEST, 'fungus': config.BUZZ_PATTERN_FUNGUS,
                'fault': config.BUZZ_PATTERN_FAULT}

class BuzzerScheduler:
    def __init__(self, clock_fn=None, command=None):
        self._clock = clock_fn or time.monotonic
        self._command = command or (lambda command: None)
        self._muted = config.BUZZER_MUTED
        self._pending = None
        self._last_global = float('-inf')
        self._class_times = {}
        self._on_times = deque()
        self._lock = threading.RLock()
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._player_loop, daemon=True, name='buzzer')
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._event.set()
        if self._thread:
            self._thread.join(timeout=4)
        self.fail_safe_off()

    @property
    def is_muted(self):
        with self._lock:
            return self._muted

    def mute(self, muted):
        with self._lock:
            self._muted = muted
            self._pending = None
            # Serialized against pulse dispatch: mute cannot be followed by a stale ON.
            if muted:
                self.fail_safe_off()

    def fail_safe_off(self):
        return self._command('BUZZ_OFF')

    def trigger(self, event_class):
        with self._lock:
            if self._muted:
                return
            if self._pending is None:
                self._pending = (event_class, self._clock())
            elif len(_PATTERN_MAP.get(event_class, [])) > len(_PATTERN_MAP.get(self._pending[0], [])):
                self._pending = (event_class, self._pending[1])
        self._event.set()

    def _reserve(self, cls, now):
        pattern = _PATTERN_MAP.get(cls, config.BUZZ_PATTERN_FAULT)
        duration = sum(on for on, _ in pattern) / 1000
        while self._on_times and self._on_times[0][0] <= now - 60:
            self._on_times.popleft()
        if (self._muted or now - self._last_global < config.BUZZ_MIN_INTERVAL_S
            or now - self._class_times.get(cls, float('-inf')) < config.BUZZ_CLASS_COOLDOWN_S
            or sum(entry[1] for entry in self._on_times) + duration > config.BUZZ_MAX_ON_PER_MIN_S):
            return None
        self._last_global = self._class_times[cls] = now
        # Record completion time conservatively so no pulse expires from the budget early.
        self._on_times.append((now + sum(on + off for on, off in pattern) / 1000, duration))
        return pattern

    def _player_loop(self):
        while not self._stop.is_set():
            self._event.wait(1)
            self._event.clear()
            if self._stop.wait(0.5):
                break
            with self._lock:
                pending, self._pending = self._pending, None
                pattern = self._reserve(pending[0], self._clock()) if pending else None
            if pattern:
                try:
                    for on, off in pattern:
                        with self._lock:
                            if self._muted or self._stop.is_set():
                                break
                            if self._command(f'BUZZ_PULSE:{on}') != 'OK':
                                break
                        if self._stop.wait((on + off) / 1000):
                            break
                finally:
                    self.fail_safe_off()

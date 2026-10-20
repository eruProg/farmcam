"""Validated, immutable per-run timings and atomic on-device storage."""
from dataclasses import dataclass, asdict
import json
import math
import os
import tempfile
from pathlib import Path


@dataclass(frozen=True)
class ScanSettings:
    scan_s: float = 60.0
    initial_delay_s: float = 5.0
    raise_s: float = 2.8
    lower_s: float = 2.8
    travel_1_2_s: float = 8.5
    travel_2_3_s: float = 8.6
    travel_3_4_s: float = 8.6
    return_s: float = None

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != set(asdict(cls())):
            raise ValueError('Supply all eight timing fields, with no extra fields')
        for key, value in data.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{key}: enter a finite number of seconds')
            if value < 0 or (value == 0 and key != 'initial_delay_s'):
                raise ValueError(f'{key}: duration must be positive')
        return cls(**{key: float(value) for key, value in data.items()})

    def to_dict(self):
        return asdict(self)


def load_settings(path):
    path = Path(path)
    return ScanSettings.parse(json.loads(path.read_text())) if path.exists() else ScanSettings()


def save_settings(path, settings):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.scan-settings-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(settings.to_dict(), stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

"""Observation only: no model, scan, threshold or actuator decisions."""
import copy
import hashlib
import threading
import time
from collections import Counter


def model_identity(entry):
    digest = hashlib.sha256()
    with open(entry['path'], 'rb') as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b''):
            digest.update(chunk)
    return dict(label=entry['label'], path=str(entry['path']),
                classes=list(entry['class_names']), sha256=digest.hexdigest())


class InferenceDiagnostics:
    def __init__(self, clock=time.monotonic, wall=time.time):
        self.clock, self.wall = clock, wall
        self.lock = threading.Lock()
        self.sample = None

    def record(self, sequence, meta, started, before, after, debug, error=None):
        sample = dict(
            frame_sequence=sequence, scan_id=meta.scan_id, station=meta.station,
            phase=getattr(meta.phase, 'name', str(meta.phase)),
            captured_at_monotonic=meta.captured_at,
            completed_at=self.wall(), completed_monotonic=self.clock(),
            duration_s=max(0, self.clock() - started),
            before_verification=dict(Counter(d[4] for d in before)),
            after_verification=dict(Counter(d[4] for d in (after or []))),
            result='error' if error else 'discarded_by_coordinator' if after is None else 'verified',
            error=error,
            class_scores=copy.deepcopy(debug.get('class_scores', {})),
            accepted_before_crop_merge=copy.deepcopy(debug.get('accepted_boxes', {})),
            confirmed=copy.deepcopy(debug.get('confirmed_boxes', {})))
        with self.lock:
            self.sample = sample

    def snapshot(self, scan):
        with self.lock:
            sample = copy.deepcopy(self.sample)
        age = max(0, self.clock() - sample['completed_monotonic']) if sample else None
        return dict(last_inference=sample, last_inference_age_s=age,
                    active_scan=scan,
                    sample_matches_scan=bool(sample and scan and scan.get('scan_id') == sample['scan_id']
                        and scan.get('phase') == sample['phase'] and scan.get('station') == sample['station']))

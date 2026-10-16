"""Read-only runtime observations, independent of inference implementation."""
import time
import uuid


class DisplayHealth:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.instance_id = str(uuid.uuid4())
        self.loop_heartbeat = None

    def tick(self):
        self.loop_heartbeat = self.clock()

    def snapshot(self, frame_timestamp, frame_present, shared):
        now = self.clock()
        return {
            'instance_id': self.instance_id,
            'camera_frame_age_s': max(0, now - frame_timestamp) if frame_present and frame_timestamp else None,
            'detection_loop_age_s': max(0, now - self.loop_heartbeat) if self.loop_heartbeat is not None else None,
            'coordinator_ready': bool(shared.get('ready')),
            'fault': shared.get('fault'),
            'pump_active': bool(shared.get('pump_active')),
        }

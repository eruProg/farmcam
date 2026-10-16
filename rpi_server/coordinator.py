"""Runtime integration; frozen inference functions remain owned by main.py."""
import logging
import threading
import time
import config
from scan_state import ScanStateMachine, ScanPhase
from persistence import PersistenceDB, CooldownManager, _iou

log = logging.getLogger(__name__)
_lock = threading.RLock()
_runtime = None
_scan_sm = _verifier = _db = _cooldown_mgr = None
_buzzer = _sms_broker = _snap_writer = None
_window = None
_wired = False
_fault = None


def wire(runtime):
    global _runtime, _scan_sm, _verifier, _db, _cooldown_mgr
    global _buzzer, _sms_broker, _snap_writer, _wired, _fault
    if _wired:
        return
    _runtime = runtime
    try:
        from verifier import DetectionVerifier
        from snapshot_writer import SnapshotWriter
        from sms_broker import SmsBroker
        from buzzer import BuzzerScheduler
        _db = PersistenceDB(config.DB_PATH)
        _db.abort_incomplete_scans()
        _cooldown_mgr = CooldownManager(_db)
        _verifier = DetectionVerifier()
        _snap_writer = SnapshotWriter(db=_db)
        _sms_broker = SmsBroker()
        _buzzer = BuzzerScheduler(command=runtime.esp32_cmd)
        for service in (_snap_writer, _sms_broker, _buzzer):
            service.start()
        _scan_sm = ScanStateMachine()
        _scan_sm.on_phase_change(_on_phase_change)
        _fault = None
        _wired = True
        _restore_stations()
        _scan_sm.start()
        _sync()
    except Exception:
        shutdown()
        raise


def get_shared_state():
    with _lock:
        return dict(scan_sm=_scan_sm, db=_db, cooldown_mgr=_cooldown_mgr,
                    sms_broker=_sms_broker, buzzer_scheduler=_buzzer,
                    fault=_fault, ready=_wired,
                    pump_active=bool(_runtime and _runtime.pump_active))


def scan_state():
    with _lock:
        return _scan_sm.state_dict() if _scan_sm else None


def tick():
    with _lock:
        if _scan_sm:
            if _window and time.monotonic() - _window['last_frame'] > 2.0:
                _window['valid'] = False
            _scan_sm.tick()
            _sync()


def capture_meta(seq, captured_at=None):
    with _lock:
        return _scan_sm.make_frame_meta(seq, captured_at) if _scan_sm else None


def accepts(meta):
    with _lock:
        return bool(_wired and meta and _scan_sm.is_detecting
                    and meta.scan_id == _scan_sm.scan_id
                    and meta.phase == _scan_sm.phase
                    and meta.station == _scan_sm.station
                    and meta.captured_at >= _scan_sm.phase_started_at
                    and meta.captured_at < _scan_sm.phase_started_at + _scan_sm.phase_duration
                    and time.monotonic() - meta.captured_at <= 2.0)


def handle_frame(frame, raw_dets, meta):
    global _fault
    with _lock:
        # Inference may have crossed the end of the window.
        tick()
        if not accepts(meta) or not _window:
            return None
        try:
            verified = _verifier.verify(raw_dets, frame, meta)
        except Exception as exc:
            _fault = 'Verification unavailable'
            _window['valid'] = False
            log.exception('Verification failed; automatic actuation disabled')
            return None
        _fault = None
        _window['frames'] += 1
        _window['last_frame'] = time.monotonic()
        # Keep at most eight full-resolution frames per window, shared between
        # detections. Track accumulation must not multiply camera memory by 256.
        snapshot_frame = None
        if verified and _window['snapshot_count'] < 8:
            snapshot_frame = frame.copy()
            _window['snapshot_count'] += 1
        for det in verified:
            det = list(det)
            while len(det) < 8:
                det.append(None if len(det) == 6 else 0)
            area = det[7] or max(0, det[2]-det[0]) * max(0, det[3]-det[1])
            det = det[:8] + [min(100.0, area * 100.0 / max(1, frame.shape[0]*frame.shape[1])) if det[4] == 'fungus' else 0.0]
            match = next((entry for entry in _window['detections']
                          if entry['det'][4] == det[4]
                          and _iou(entry['det'][:4], det[:4]) >= config.TRACK_IOU_RESCAN), None)
            if match is None:
                if len(_window['detections']) >= 256:
                    _window['valid'] = False
                    continue
                _window['detections'].append(dict(det=det, frame=snapshot_frame, meta=meta))
            elif det[5] > match['det'][5]:
                # Keep the snapshot and its corresponding geometry together.
                if snapshot_frame is not None:
                    match.update(det=det, frame=snapshot_frame, meta=meta)
                else:
                    match['det'][5] = det[5]
        return verified


def automatic_ready(station=None, scan_id=None):
    with _lock:
        return bool(_wired and not _fault and _window and _window['valid']
                    and _scan_sm.is_detecting and _scan_sm.remaining > 0
                    and (station is None or station == _scan_sm.station)
                    and (scan_id is None or scan_id == _scan_sm.scan_id))


def overlay(detections):
    """Adapt verified inference rows to the existing MJPEG drawing contract."""
    colors = {'fungus': (0,165,255), 'whiteflies': (0,255,255),
              'whitefly': (0,255,255), 'worm': (0,0,255)}
    return [tuple(det[:4]) + (f'{det[4]} {det[5]:.2f}', colors.get(det[4], (0,255,0)),
                             det[6] if len(det) > 6 else None) for det in detections]


def send_sms(alert_type, station, message):
    if not _wired or not _sms_broker or not config.FARMER_PHONE:
        return False
    if not _cooldown_mgr.claim('sms', config.SMS_COOLDOWN_S):
        return False
    return bool(_sms_broker.send(config.FARMER_PHONE,
                                f'FarmCam L{station} {alert_type}: {message}'[:160]))


def _finish_window():
    global _window
    window, _window = _window, None
    if not window:
        return
    if not window['valid'] or not window['frames']:
        _db.abort_scan(window['id'])
        return
    entries = window['detections']
    committed_ids = []
    new_ids = _db.commit_scan(window['id'], window['station'], window['position'],
                             [entry['det'] for entry in entries],
                             committed_track_ids=committed_ids)
    for entry, track_id in zip(entries, committed_ids):
        det = entry['det']
        if entry['frame'] is not None:
            _snap_writer.submit(entry['frame'], [det], track_id, entry['meta'])
        if track_id in new_ids:
            _buzzer.trigger(det[4])
    _restore_stations()


def _on_phase_change(phase, station):
    global _window, _fault
    try:
        _finish_window()
        _verifier.reset_tracker()
        if _runtime:
            _runtime.display_detections = []
            _runtime.scan_phase_changed(phase, station)
        if phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER):
            scan_id = _db.begin_scan(station, phase.position(), _scan_sm.scan_id)
            _window = dict(id=scan_id, station=station, position=phase.position(),
                           frames=0, valid=True, last_frame=time.monotonic(), detections=[], snapshot_count=0)
    except Exception:
        _fault = 'Scan persistence unavailable'
        if _window:
            _window['valid'] = False
        log.exception('Scan transition failed; automatic actuation disabled')


def _restore_stations():
    if _runtime:
        _runtime.restore_station_summaries(_db.get_station_summary())


def _sync():
    if _runtime and _scan_sm:
        _runtime.active_station_id = _scan_sm.station
        _runtime.buzzer_muted = _buzzer.is_muted if _buzzer else True
        _runtime.current_state = {
            ScanPhase.DETECT_UPPER: 'DETECT_A', ScanPhase.DETECT_LOWER: 'DETECT_B',
            ScanPhase.TRANSIT_TO_LOWER: 'PAUSE_5', ScanPhase.SETTLING: 'PAUSE_5',
            ScanPhase.TRANSIT_TO_UPPER: 'MOVING', ScanPhase.MOVING_TO_STATION: 'MOVING',
        }.get(_scan_sm.phase, 'IDLE')


def shutdown():
    global _wired, _window, _scan_sm, _db, _verifier, _cooldown_mgr
    global _sms_broker, _snap_writer, _buzzer, _runtime
    with _lock:
        _wired = False
        if _window and _db:
            _db.abort_scan(_window['id'])
        _window = None
        if _scan_sm:
            _scan_sm.stop()
        for service in (_buzzer, _sms_broker, _snap_writer):
            if service:
                service.stop()
        _scan_sm = _db = _verifier = _cooldown_mgr = None
        _sms_broker = _snap_writer = _buzzer = _runtime = None

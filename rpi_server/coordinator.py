"""Runtime integration; frozen inference functions remain owned by main.py."""
import logging
import threading
import time
import config
from scan_settings import ScanSettings, load_settings, save_settings
from scan_state import ScanStateMachine, ScanPhase
from persistence import PersistenceDB, CooldownManager, _iou

log = logging.getLogger(__name__)
_lock = threading.RLock()
_runtime = None
_scan_sm = _verifier = _db = _cooldown_mgr = None
_buzzer = _sms_broker = _snap_writer = _sms_router = None
_window = None
# The current lettuce visit (one station in one scan run: its upper + lower
# window). Spray decisions and the app's live card count over the visit.
_visit = None
_wired = False
_fault = None


def wire(runtime):
    global _runtime, _scan_sm, _verifier, _db, _cooldown_mgr
    global _buzzer, _sms_broker, _snap_writer, _sms_router, _wired, _fault
    if _wired:
        return
    _runtime = runtime
    try:
        from verifier import DetectionVerifier
        from snapshot_writer import SnapshotWriter
        from sms_broker import SmsBroker
        from sms_router import SmsRouter
        from buzzer import BuzzerScheduler
        _db = PersistenceDB(config.DB_PATH)
        _db.abort_incomplete_scans()
        _cooldown_mgr = CooldownManager(_db)
        import wf_classifier
        # Second deep-learning stage for whiteflies (None: colour rules as before).
        classifier = wf_classifier.load(config.WF_CLS_MODEL)
        print(f'Whitefly classifier: {classifier.path.name if classifier else "not installed, colour checks"}')
        _verifier = DetectionVerifier(classifier=classifier, zoom=getattr(runtime, 'zoom_crops', None))
        _snap_writer = SnapshotWriter(db=_db)
        _sms_broker = SmsBroker()
        _sms_router = SmsRouter(_sms_broker, runtime.esp32_cmd, on_failed=lambda err: notify(
            'error', 'Error: SMS not sent', err or 'The ESP32 / SIM800L did not send the SMS.'))
        _buzzer = BuzzerScheduler(command=runtime.esp32_cmd)
        for service in (_snap_writer, _sms_broker, _buzzer):
            service.start()
        _scan_sm = ScanStateMachine()
        _scan_sm.on_phase_change(_on_phase_change)
        _fault = None
        _wired = True
        _restore_stations()
        try:
            _scan_sm.settings = load_settings(config.DATA_DIR / 'scan_settings.json')
        except (ValueError, OSError):
            log.exception('Saved scan timings unavailable; enter timings before starting')
        _sync()
    except Exception:
        shutdown()
        raise


def get_shared_state():
    with _lock:
        return dict(scan_sm=_scan_sm, db=_db, cooldown_mgr=_cooldown_mgr,
                    scan_state=_scan_sm.state_dict() if _scan_sm else None,
                    sms_broker=_sms_broker, sms_router=_sms_router, buzzer_scheduler=_buzzer,
                    fault=_fault, ready=_wired,
                    pump_active=bool(_runtime and _runtime.pump_active))


def scan_state():
    with _lock:
        return _scan_sm.state_dict() if _scan_sm else None


def timing_settings():
    with _lock:
        if not _wired or not _scan_sm:
            raise RuntimeError('Scan controller unavailable')
        return dict(settings=_scan_sm.settings.to_dict(), state=_scan_sm.state_dict())


def start_scan(data):
    settings = ScanSettings.parse(data)
    with _lock:
        if not _wired or not _scan_sm:
            raise RuntimeError('Scan controller unavailable')
        if _scan_sm.phase is not ScanPhase.IDLE:
            raise RuntimeError('A scan is already running')
        save_settings(config.DATA_DIR / 'scan_settings.json', settings)
        _scan_sm.start(settings)
        _sync()
        return _scan_sm.state_dict()


def stop_scan():
    global _window, _visit
    with _lock:
        if not _wired or not _scan_sm:
            raise RuntimeError('Scan controller unavailable')
        if _window:
            _db.abort_scan(_window['id'])
            _window = None
        _visit = None
        _scan_sm.stop()
        _sync()
        return _scan_sm.state_dict()


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
            _set_fault('Verification unavailable')
            _window['valid'] = False
            log.exception('Verification failed; automatic actuation disabled')
            return None
        _fault = None
        _window['frames'] += 1
        _window['last_frame'] = time.monotonic()
        new_entries = []
        # Keep at most eight full-resolution frames per window, shared between
        # detections. Track accumulation must not multiply camera memory by 256.
        # After that, one more copy (the newest frame with detections) is kept
        # for later detections: the camera is still within a window, so it shows
        # the same view, and every detection gets a photo.
        snapshot_frame = None
        if verified and _window['snapshot_count'] < 8:
            snapshot_frame = frame.copy()
            _window['snapshot_count'] += 1
        elif verified:
            _window['last_frame_img'] = frame.copy()
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
                entry = dict(det=det, frame=snapshot_frame, meta=meta)
                _window['detections'].append(entry)
                new_entries.append(entry)
            elif det[5] > match['det'][5]:
                # Keep the snapshot and its corresponding geometry together.
                if snapshot_frame is not None:
                    match.update(det=det, frame=snapshot_frame, meta=meta)
                else:
                    match['det'][5] = det[5]
        if new_entries:
            _notify_live(new_entries)
        return verified


def _norm(cls):
    return 'whiteflies' if cls == 'whitefly' else str(cls)


def _notify_live(entries):
    """Real-time alert: the first time a class is confirmed in this window and
    it is not an already-known track of this lettuce part, tell the phone now
    (not when the window ends). Called with _lock held."""
    station, position = _window['station'], _window['position']
    notified = _window.setdefault('notified', set())
    for entry in entries:
        det = entry['det']
        cls = _norm(det[4])
        if cls in notified:
            continue
        try:
            known = _db.find_active_track(station, position, cls, [int(v) for v in det[:4]])
        except Exception:
            known = None
        if known:
            continue
        notified.add(cls)
        title, one, _ = _EVENT_TEXT.get(cls, (f'{cls.title()} detected', cls, cls))
        if cls == 'fungus':
            body = f'Lettuce {station}, {position} part: fungus spot found (scan in progress).'
        else:
            body = f'Lettuce {station}, {position} part: {one} found (scan in progress).'
        notify(cls, title, body, station, None)


def _visit_windows():
    """Detections of the current visit: finished windows + the open one."""
    if not _visit:
        return []
    windows = list(_visit['windows'])
    if _window and _window['station'] == _visit['station']:
        windows.append([entry['det'] for entry in _window['detections']])
    return windows


def _visit_summary():
    counts, fungus_pct = {}, 0.0
    for dets in _visit_windows():
        window_fungus = 0.0
        for det in dets:
            cls = _norm(det[4])
            counts[cls] = counts.get(cls, 0) + 1
            if cls == 'fungus':
                window_fungus += float(det[8]) if len(det) > 8 and det[8] else 0.0
        fungus_pct = max(fungus_pct, window_fungus)      # parts are separate views
    return counts, fungus_pct


def _spray_reason_locked():
    if not _visit:
        return None
    counts, fungus_pct = _visit_summary()
    station = _visit['station']
    worms, whiteflies = counts.get('worm', 0), counts.get('whiteflies', 0)
    if whiteflies >= config.SPRAY_WHITEFLIES_MIN:
        return f'{whiteflies} whiteflies on Lettuce {station}'
    if worms >= config.SPRAY_WORMS_MIN:
        return f'{worms} worms on Lettuce {station}'
    if fungus_pct >= config.SEV_FUNGUS_HIGH_PCT:
        return f'fungus on {fungus_pct:.0f}% of Lettuce {station}'
    return None


def take_spray_reason(station):
    """Client rule, once per lettuce visit: >= SPRAY_WHITEFLIES_MIN confirmed
    whiteflies, >= SPRAY_WORMS_MIN worms (upper + lower part together), or
    fungus "high". Returns the reason the first time it holds, else None."""
    with _lock:
        if not _visit or _visit['station'] != station or _visit['spray_taken']:
            return None
        reason = _spray_reason_locked()
        if reason:
            _visit['spray_taken'] = True
        return reason


def live_state():
    """What the camera has confirmed during the current lettuce visit, for the
    app's "Detecting now" card (before anything is written to history)."""
    with _lock:
        if not (_wired and _scan_sm and _scan_sm.is_detecting and _visit):
            return None
        counts, fungus_pct = _visit_summary()
        items = sorted(({'class': _norm(d[4]), 'confidence': round(float(d[5]), 2)}
                        for dets in _visit_windows() for d in dets),
                       key=lambda item: -item['confidence'])[:20]
        return dict(station=_visit['station'], position=_scan_sm.position, since=_visit['since'],
                    counts=counts, fungus_pct=round(fungus_pct, 1), items=items,
                    spray_reason=_spray_reason_locked())


def automatic_ready(station=None, scan_id=None):
    with _lock:
        return bool(_wired and not _fault and _window and _window['valid']
                    and _scan_sm.is_detecting and _scan_sm.remaining > 0
                    and (station is None or station == _scan_sm.station)
                    and (scan_id is None or scan_id == _scan_sm.scan_id))


def display_filter(frame, detections, meta):
    """Per-frame verifier gates (no tracker) so the live feed hides boxes
    the verifier would reject. Unwired/failing verifier: show unchanged."""
    verifier = _verifier
    if verifier is None:
        return detections
    try:
        return verifier.filter_static(detections, frame, meta)
    except Exception:
        log.exception('Display filter failed; showing unverified boxes')
        return detections


def overlay(detections):
    """Adapt verified inference rows to the existing MJPEG drawing contract."""
    colors = {'fungus': (0,165,255), 'whiteflies': (0,255,255),
              'whitefly': (0,255,255), 'worm': (0,0,255)}
    return [tuple(det[:4]) + (f'{det[4]} {det[5]:.2f}', colors.get(det[4], (0,255,0)),
                             det[6] if len(det) > 6 else None) for det in detections]


_last_fault_event = 0.0
_EVENT_TEXT = {  # class -> (title, one, many)
    'whiteflies': ('Whitefly detected', 'whitefly', 'whiteflies'),
    'whitefly': ('Whitefly detected', 'whitefly', 'whiteflies'),
    'worm': ('Worm detected', 'worm', 'worms'),
    'fungus': ('Fungus detected', 'spot', 'spots'),
}


def notify(kind, title, body='', station=None, severity=None):
    """Store an event; the phone app turns it into a notification. Never raises."""
    db = _db
    if db is None:
        return None
    try:
        return db.add_event(kind, title, body, station, severity)
    except Exception:
        log.exception('Could not store event %s', title)
        return None


def _set_fault(message):
    """Disable automatic actuation and tell the phone once (at most every 10 min)."""
    global _fault, _last_fault_event
    first = _fault is None
    _fault = message
    if first and time.monotonic() - _last_fault_event > 600:
        _last_fault_event = time.monotonic()
        notify('error', 'Error: Scan stopped', f'{message}. Check the camera and the Pi.')


def _notify_detections(station, position, dets):
    """One event per class for the tracks that are NEW in this scan window."""
    by_class = {}
    for det in dets:
        by_class.setdefault(str(det[4]), []).append(det)
    for cls, items in by_class.items():
        title, one, many = _EVENT_TEXT.get(cls, (f'{cls.title()} detected', cls, cls))
        count = len(items)
        if cls == 'fungus':
            coverage = sum(float(d[8]) if len(d) > 8 and d[8] else 0.0 for d in items)
            severity = ('high' if coverage >= config.SEV_FUNGUS_HIGH_PCT
                        else 'medium' if coverage >= 5.0 else 'low')
            body = (f'Lettuce {station}, {position} part: {count} {one if count == 1 else many}, '
                    f'{coverage:.1f}% of the leaf ({severity}).')
        else:
            severity = 'high' if count >= config.SEV_PEST_HIGH else 'medium'
            body = f'Lettuce {station}, {position} part: {count} {one if count == 1 else many} ({severity}).'
        notify('whiteflies' if cls == 'whitefly' else cls, title, body, station, severity)


def send_sms(alert_type, station, message):
    # The router picks the ESP32 firmware's SMS method. An empty
    # FARMCAM_FARMER_PHONE only matters for v3.0 (v2.x has the number built in).
    if not _wired or not _sms_router:
        return False
    if not _cooldown_mgr.claim('sms', config.SMS_COOLDOWN_S):
        return False
    # message is the farmer's whole text (main._spray_sms); alert_type and
    # station only matter for the log and the cooldown.
    return _sms_router.send(message)['ok']


def _finish_window():
    global _window
    window, _window = _window, None
    if not window:
        return
    if _visit and window['station'] == _visit['station'] and window['detections']:
        _visit['windows'].append([entry['det'] for entry in window['detections']])
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
        frame = entry['frame'] if entry['frame'] is not None else window.get('last_frame_img')
        if frame is not None:
            _snap_writer.submit(frame, [det], track_id, entry['meta'])
        if track_id in new_ids:
            _buzzer.trigger(det[4])
    # Classes already announced live in this window are not announced again.
    already = window.get('notified', set())
    _notify_detections(window['station'], window['position'],
                       [entry['det'] for entry, track_id in zip(entries, committed_ids)
                        if track_id in new_ids and _norm(entry['det'][4]) not in already])
    _restore_stations()


def _on_phase_change(phase, station):
    global _window, _fault, _visit
    try:
        _finish_window()
        _verifier.reset_tracker()
        if _runtime:
            _runtime.display_detections = []
            _runtime.scan_phase_changed(phase, station)
        if phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER):
            key = (getattr(_scan_sm, 'run_id', None), station)
            if not _visit or _visit['key'] != key:      # a new lettuce: start counting again
                _visit = dict(key=key, station=station, windows=[], spray_taken=False, since=time.time())
            scan_id = _db.begin_scan(station, phase.position(), _scan_sm.scan_id)
            _window = dict(id=scan_id, station=station, position=phase.position(),
                           frames=0, valid=True, last_frame=time.monotonic(), detections=[], snapshot_count=0)
    except Exception:
        _set_fault('Scan persistence unavailable')
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
    global _sms_broker, _snap_writer, _buzzer, _runtime, _sms_router
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
        _sms_broker = _snap_writer = _buzzer = _runtime = _sms_router = None

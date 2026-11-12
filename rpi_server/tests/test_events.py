import urllib.error

from fastapi import FastAPI
from fastapi.testclient import TestClient

import coordinator
import esp32_discovery as ed
from app_api import register_app_api
from persistence import PersistenceDB
from sms_router import SmsRouter


def test_events_table_paging_and_new_phone_start(tmp_path):
    db = PersistenceDB(str(tmp_path / 'f.db'))
    assert db.events_after(-1) == ([], 0)
    ids = [db.add_event('worm', f'Worm detected {i}', station=1, severity='medium') for i in range(5)]
    events, last = db.events_after(-1)
    assert events == [] and last == ids[-1]                 # a new phone starts at the newest
    events, last = db.events_after(ids[1], limit=2)
    assert [e['id'] for e in events] == ids[2:4] and last == ids[-1]
    assert events[0]['title'] == 'Worm detected 2' and events[0]['station'] == 1


def test_old_events_are_pruned(tmp_path):
    db = PersistenceDB(str(tmp_path / 'f.db'))
    db.add_event('test', 'old', now=1000.0)
    db.add_event('test', 'new', now=1000.0 + db.EVENT_KEEP_S + 1)
    assert [e['title'] for e in db.events_after(0)[0]] == ['new']


class FakeDb:
    def __init__(self):
        self.events = []

    def add_event(self, *args):
        self.events.append(args)
        return len(self.events)


def test_detection_events_have_alert_titles(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(coordinator, '_db', db)
    wf = [0, 0, 5, 5, 'whiteflies', .7, None, 0, 0.0]
    fungus = [0, 0, 30, 30, 'fungus', .5, None, 0, 3.0]
    coordinator._notify_detections(2, 'upper', [wf, list(wf), list(wf), fungus, list(fungus)])
    titles = {e[1]: e for e in db.events}
    assert titles['Whitefly detected'][2] == 'Lettuce 2, upper part: 3 whiteflies (high).'
    assert titles['Whitefly detected'][0] == 'whiteflies' and titles['Whitefly detected'][4] == 'high'
    assert titles['Fungus detected'][2] == 'Lettuce 2, upper part: 2 spots, 6.0% of the leaf (medium).'
    db.events.clear()
    coordinator._notify_detections(1, 'lower', [[0, 0, 9, 9, 'worm', .9, None, 0, 0.0]])
    assert db.events == [('worm', 'Worm detected', 'Lettuce 1, lower part: 1 worm (medium).', 1, 'medium')]


def test_only_new_tracks_notify_at_the_end_of_a_scan_window(monkeypatch):
    calls = []

    class Db(FakeDb):
        def commit_scan(self, scan_id, station, position, dets, committed_track_ids):
            committed_track_ids.extend(['old', 'new'])
            return ['new']

        def get_station_summary(self):
            return []

    db = Db()
    monkeypatch.setattr(coordinator, '_db', db)
    monkeypatch.setattr(coordinator, '_snap_writer', type('S', (), {'submit': lambda *a: None})())
    monkeypatch.setattr(coordinator, '_buzzer', type('B', (), {'trigger': lambda self, c: calls.append(c)})())
    monkeypatch.setattr(coordinator, '_runtime', None)
    worm = [0, 0, 9, 9, 'worm', .9, None, 0, 0.0]
    wf = [20, 20, 25, 25, 'whiteflies', .8, None, 0, 0.0]
    monkeypatch.setattr(coordinator, '_window', dict(
        id='s', station=3, position='lower', frames=4, valid=True,
        detections=[dict(det=worm, frame=None, meta=None), dict(det=wf, frame=None, meta=None)]))
    coordinator._finish_window()
    assert [e[1] for e in db.events] == ['Whitefly detected'] and calls == ['whiteflies']


def test_scan_fault_notifies_once(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(coordinator, '_db', db)
    monkeypatch.setattr(coordinator, '_fault', None)
    monkeypatch.setattr(coordinator, '_last_fault_event', 0.0)
    coordinator._set_fault('Verification unavailable')
    coordinator._set_fault('Verification unavailable')
    assert [e[1] for e in db.events] == ['Error: Scan stopped']


def test_esp32_down_after_two_minutes_then_back():
    now = [1000.0]
    changes = []
    monitor = ed.Esp32Monitor('10.0.0.5', 80, lambda: '10.0.0.2', probe_fn=lambda ip, port: False,
                              clock=lambda: now[0], on_status=lambda up, why: changes.append(up))
    monitor.last_scan = now[0]                 # no network scan in this test
    for _ in range(8):                         # checks at 0..105 s: still within 2 minutes
        monitor.check_once()
        now[0] += 15
    assert changes == []
    monitor.check_once()                       # 120 s without an answer
    now[0] += 15
    monitor.check_once()
    assert changes == [False]                  # reported once
    monitor.note_ok()
    assert changes == [False, True]


def test_sms_failure_is_reported_once():
    failures = []

    def get_json(path):
        raise urllib.error.HTTPError('x', 404, 'nf', {}, None) if path.startswith('/sms') else OSError('down')

    router = SmsRouter(None, lambda *a, **k: None, get_json=get_json, on_failed=failures.append)
    router.send('test')
    router.status()
    assert len(failures) == 1


def test_events_endpoints(tmp_path):
    db = PersistenceDB(str(tmp_path / 'f.db'))
    app = FastAPI()
    register_app_api(app, lambda: {'db': db}, None)
    client = TestClient(app)
    start = client.get('/api/events').json()
    assert start == {'events': [], 'last_id': 0}
    assert client.post('/api/events/test').json()['ok']
    body = client.get('/api/events', params={'after': start['last_id']}).json()
    assert [e['title'] for e in body['events']] == ['Test notification'] and body['last_id'] == 1
    assert TestClient(_app_without_db()).get('/api/events').status_code == 503


def _app_without_db():
    app = FastAPI()
    register_app_api(app, lambda: {}, None)
    return app

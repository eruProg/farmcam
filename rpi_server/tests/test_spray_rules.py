import time
from types import SimpleNamespace

import pytest

import coordinator
from app_api import build_app_state
from persistence import PersistenceDB


def det(cls, x=0, conf=0.8, coverage=0.0):
    return [x, x, x + 8, x + 8, cls, conf, None, 0, coverage]


class FakeDb:
    def __init__(self, known=None):
        self.events, self.known = [], known

    def add_event(self, *args):
        self.events.append(args)
        return len(self.events)

    def find_active_track(self, station, position, cls, bbox):
        return self.known


@pytest.fixture
def scan(monkeypatch):
    """Lettuce 2 being scanned: lower part already done, upper part open."""
    db = FakeDb()
    monkeypatch.setattr(coordinator, '_db', db)
    monkeypatch.setattr(coordinator, '_wired', True)
    monkeypatch.setattr(coordinator, '_scan_sm', SimpleNamespace(is_detecting=True, position='upper', run_id='r1'))
    monkeypatch.setattr(coordinator, '_visit', dict(key=('r1', 2), station=2, windows=[], spray_taken=False,
                                                    since=time.time()))
    monkeypatch.setattr(coordinator, '_window', dict(id='w2', station=2, position='upper', frames=3, valid=True,
                                                     detections=[]))
    return db


def add(*dets):
    coordinator._window['detections'].extend(dict(det=d, frame=None, meta=None) for d in dets)


def test_three_whiteflies_over_both_parts_spray_once(scan):
    coordinator._visit['windows'].append([det('whiteflies', 0), det('whiteflies', 50)])   # lower part
    assert coordinator.take_spray_reason(2) is None                                      # only 2 so far
    add(det('whiteflies', 100))                                                          # upper part
    assert coordinator.take_spray_reason(2) == '3 whiteflies on Lettuce 2'
    assert coordinator.take_spray_reason(2) is None                                      # once per visit


def test_two_worms_do_not_spray_three_do(scan):
    add(det('worm', 0), det('worm', 50))
    assert coordinator.take_spray_reason(2) is None
    add(det('worm', 100))
    assert coordinator.take_spray_reason(2) == '3 worms on Lettuce 2'


def test_mixed_types_are_counted_separately(scan):
    add(det('worm', 0), det('worm', 50), det('whiteflies', 100), det('whiteflies', 150))
    assert coordinator.take_spray_reason(2) is None


def test_high_fungus_sprays(scan):
    add(det('fungus', 0, coverage=9.0), det('fungus', 50, coverage=7.0))
    assert coordinator.take_spray_reason(2) == 'fungus on 16% of Lettuce 2'


def test_only_the_lettuce_being_scanned(scan):
    add(*(det('worm', i * 20) for i in range(3)))
    assert coordinator.take_spray_reason(3) is None


def test_live_state_for_the_app(scan):
    coordinator._visit['windows'].append([det('worm', 0)])
    add(det('whiteflies', 0, conf=0.71), det('whiteflies', 40, conf=0.66))
    live = coordinator.live_state()
    assert live['station'] == 2 and live['position'] == 'upper'
    assert live['counts'] == {'worm': 1, 'whiteflies': 2}
    assert live['items'][0] == {'class': 'worm', 'confidence': 0.8}      # strongest first
    assert {'class': 'whiteflies', 'confidence': 0.71} in live['items']
    assert live['spray_reason'] is None
    coordinator._scan_sm.is_detecting = False
    assert coordinator.live_state() is None                 # travelling: no live card


def test_alert_is_sent_when_first_confirmed(scan):
    first = dict(det=det('whiteflies', 0), frame=None, meta=None)
    coordinator._notify_live([first])
    coordinator._notify_live([dict(det=det('whiteflies', 60), frame=None, meta=None)])
    assert [(e[0], e[1]) for e in scan.events] == [('whiteflies', 'Whitefly detected')]   # once per window
    assert scan.events[0][2] == 'Lettuce 2, upper part: whitefly found (scan in progress).'


def test_already_known_track_is_not_announced_again(scan):
    scan.known = 'track-1'
    coordinator._notify_live([dict(det=det('worm'), frame=None, meta=None)])
    assert scan.events == []


def test_window_end_skips_classes_already_announced(monkeypatch, scan):
    class Db(FakeDb):
        def commit_scan(self, scan_id, station, position, dets, committed_track_ids):
            committed_track_ids.extend(['a', 'b'])
            return ['a', 'b']

        def get_station_summary(self):
            return []

    db = Db()
    monkeypatch.setattr(coordinator, '_db', db)
    monkeypatch.setattr(coordinator, '_snap_writer', SimpleNamespace(submit=lambda *a: None))
    monkeypatch.setattr(coordinator, '_buzzer', SimpleNamespace(trigger=lambda c: None))
    monkeypatch.setattr(coordinator, '_runtime', None)
    add(det('whiteflies', 0), det('worm', 50))
    coordinator._window['notified'] = {'whiteflies'}
    coordinator._finish_window()
    assert [e[1] for e in db.events] == ['Worm detected']
    assert coordinator._visit['windows'][-1][0][4] == 'whiteflies'   # kept for the visit count


def test_find_active_track(tmp_path):
    db = PersistenceDB(str(tmp_path / 'f.db'))
    scan_id = db.begin_scan(2, 'upper')
    db.commit_scan(scan_id, 2, 'upper', [[10, 10, 30, 30, 'worm', 0.9, None, 0, 0.0]])
    assert db.find_active_track(2, 'upper', 'worm', [11, 10, 31, 30])
    assert db.find_active_track(2, 'upper', 'whiteflies', [11, 10, 31, 30]) is None
    assert db.find_active_track(2, 'lower', 'worm', [11, 10, 31, 30]) is None


def test_app_state_carries_live_and_redraws_when_it_changes():
    live = {'station': 2, 'position': 'upper', 'counts': {'worm': 1}, 'fungus_pct': 0, 'spray_reason': None,
            'items': [], 'since': 0}
    runtime = SimpleNamespace(
        SERVER_BUILD='b', MODEL_COLOR_ORDER='split', start_time=0, station_payloads=lambda: [],
        pump_state=lambda: {}, esp32_state=lambda: {}, health_state=lambda: {},
        capture_state=lambda: {}, live_state=lambda: live)
    first = build_app_state({}, runtime)
    assert first['live']['counts'] == {'worm': 1}
    live['counts'] = {'worm': 2}
    assert build_app_state({}, runtime)['rev'] != first['rev']

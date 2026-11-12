"""The full-screen pop-up (HDMI monitor + app): spraying pesticide or moving
to a lettuce, never detections. activity_state() is loaded from main.py on
its own, without starting the camera or the models."""
import ast
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app_api import build_app_state, register_app_api


def load_activity(**globals_):
    source = ast.parse((Path(__file__).parent.parent / 'main.py').read_text(encoding='utf-8-sig'))
    keep = [node for node in source.body
            if (isinstance(node, ast.FunctionDef) and node.name == 'activity_state')
            or (isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'MOVING_PHASES'
                                                       for t in node.targets))]
    ns = dict(time=SimpleNamespace(time=lambda: 1000.0), pump_active=False, manual_pump_on=False,
              _spray_shown_until=0.0, coordinator=SimpleNamespace(scan_state=lambda: None))
    ns.update(globals_)
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'main.py', 'exec'), ns)
    return ns


def test_spray_shows_while_pumping_and_for_a_while_after():
    assert load_activity(pump_active=True)['activity_state']()['title'] == 'Spraying pesticide'
    assert load_activity(manual_pump_on=True)['activity_state']()['kind'] == 'spray'
    after = load_activity(_spray_shown_until=1005.0)['activity_state']
    assert after(now=1004.0)['kind'] == 'spray' and after(now=1006.0) is None


def test_moving_and_returning_texts():
    activity = load_activity()['activity_state']
    assert activity({'phase': 'MOVING_TO_STATION', 'station': 3}) == {
        'kind': 'moving', 'title': 'Moving to Lettuce 3', 'body': 'The camera is moving to the next lettuce.'}
    assert activity({'phase': 'SETTLING', 'station': 2})['title'] == 'Moving to Lettuce 2'
    assert activity({'phase': 'RETURNING', 'station': 4})['title'] == 'Returning to Lettuce 1'


def test_no_popup_for_detecting_or_idle_and_spray_wins():
    activity = load_activity()['activity_state']
    assert activity({'phase': 'DETECT_UPPER', 'station': 1}) is None
    assert activity({'phase': 'IDLE', 'station': 1}) is None
    assert load_activity(pump_active=True)['activity_state'](
        {'phase': 'MOVING_TO_STATION', 'station': 2})['kind'] == 'spray'


def test_app_state_carries_activity_and_changes_rev():
    current = [None]
    rt = SimpleNamespace(
        SERVER_BUILD='b', MODEL_COLOR_ORDER='split', start_time=0, station_payloads=lambda: [],
        pump_state=lambda: {'active': False}, esp32_state=lambda: {}, health_state=lambda: {},
        capture_state=lambda: {}, activity_state=lambda scan=None: current[0])
    idle = build_app_state({}, rt)
    current[0] = {'kind': 'moving', 'title': 'Moving to Lettuce 2', 'body': '...'}
    moving = build_app_state({}, rt)
    assert idle['activity'] is None and moving['activity']['title'] == 'Moving to Lettuce 2'
    assert idle['rev'] != moving['rev']
    app = FastAPI()
    register_app_api(app, lambda: {}, rt)
    assert TestClient(app).get('/api/app/state').json()['activity']['kind'] == 'moving'

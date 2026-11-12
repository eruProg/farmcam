from scan_settings import ScanSettings
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest
import coordinator as c
from persistence import PersistenceDB
from scan_state import ScanStateMachine

@pytest.fixture
def runtime(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(c.time,'monotonic',lambda: now[0])
    c._db = PersistenceDB(str(tmp_path/'farm.db'))
    c._runtime = SimpleNamespace(pump_active=False, latest_detections=[],
        scan_phase_changed=Mock(), restore_station_summaries=Mock())
    c._verifier = Mock()
    c._verifier.verify.side_effect = lambda dets, frame, meta: dets
    c._snap_writer = Mock()
    c._buzzer = Mock()
    c._scan_sm = ScanStateMachine(time_fn=lambda: now[0])
    c._scan_sm.on_phase_change(c._on_phase_change)
    c._wired = True
    c._fault = None
    c._scan_sm.start(ScanSettings(scan_s=30, initial_delay_s=0, return_s=10))
    yield now
    c.shutdown()

def test_commit_once_at_end_and_snapshot_real_id(runtime):
    frame = np.zeros((100,100,3), np.uint8)
    meta = c.capture_meta(1)
    det = [20,20,40,40,'worm',.9]
    c.handle_frame(frame,[det],meta)
    assert c._db.get_tracks_page()['total'] == 0
    for second in range(1,30):
        runtime[0] = 100 + second
        c.handle_frame(frame,[],c.capture_meta(second+1))
    runtime[0] = 130
    c.tick()
    track = c._db.get_tracks_page()['items'][0]
    assert c._snap_writer.submit.call_args.args[2] == track['id']
    c._buzzer.trigger.assert_called_once_with('worm')
    assert not c.accepts(meta)
    assert not c.accepts(replace(meta, scan_id='wrong'))

def test_every_detection_gets_a_photo_after_the_first_eight_frames(runtime):
    frame = np.zeros((200,400,3), np.uint8)
    for i in range(12):                     # a new whitefly in each of 12 frames
        runtime[0] = 100 + i
        x = 10 + i * 30
        c.handle_frame(frame,[[x,10,x+8,18,'whiteflies',.9]],c.capture_meta(i+1))
    for second in range(12,30):
        runtime[0] = 100 + second
        c.handle_frame(frame,[],c.capture_meta(second+1))
    runtime[0] = 130
    c.tick()
    calls = c._snap_writer.submit.call_args_list
    assert c._db.get_tracks_page(page_size=50)['total'] == 12
    assert len(calls) == 12 and all(call.args[0] is not None for call in calls)


def test_close_up_preview_is_centred_and_kept_inside_the_frame():
    from snapshot_writer import close_up
    frame = np.zeros((960,1280,3), np.uint8)
    frame[500:510, 600:610] = 255            # a 10 px whitefly
    thumb = close_up(frame, [600,500,610,510,'whiteflies',.9])
    assert thumb.shape == (240,320,3)
    ys, xs = np.nonzero(thumb[:,:,0] > 128)
    assert abs(xs.mean() - 160) < 6 and abs(ys.mean() - 120) < 6   # zoomed in, centred
    assert xs.max() - xs.min() > 15                                 # bigger than in the frame
    corner = close_up(frame, [0,0,10,10,'worm',.9])                  # at the frame edge
    assert corner.shape == (240,320,3)
    assert close_up(frame, None).shape == (240,320,3)


def test_inference_crossing_boundary_is_discarded(runtime):
    meta = c.capture_meta(1)
    runtime[0] = 130
    assert c.handle_frame(np.zeros((20,20,3)),[],meta) is None
    assert c._db.get_tracks_page()['total'] == 0

def test_verifier_failure_and_camera_gap_abort(runtime):
    c._verifier.verify.side_effect = ValueError('bad output')
    assert c.handle_frame(np.zeros((20,20,3)),[],c.capture_meta(1)) is None
    assert not c.automatic_ready()
    runtime[0] = 130
    c.tick()
    with c._db._conn() as conn:
        assert conn.execute('SELECT status FROM scans').fetchone()[0] == 'aborted'

def test_verified_overlay_preserves_polygon_and_color_contract():
    polygon = [[1,2],[3,4],[5,6]]
    row = c.overlay([[1,2,30,40,'fungus',.9,polygon,20]])[0]
    assert row == (1,2,30,40,'fungus 0.90',(0,165,255),polygon)


def test_control_endpoints_stop_abort_and_restart(runtime, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api_extensions import register_extensions
    from scan_settings import load_settings
    monkeypatch.setattr(c.config, 'DATA_DIR', tmp_path)
    app = FastAPI()
    register_extensions(app, c.get_shared_state)
    client = TestClient(app)
    profile = ScanSettings(return_s=14).to_dict()
    meta = c.capture_meta(1)
    assert client.post('/api/scan/start', json=profile).status_code == 409
    assert not (tmp_path/'scan_settings.json').exists()
    assert client.post('/api/scan/stop').json()['phase'] == 'IDLE'
    assert not c.accepts(meta) and not c.automatic_ready()
    with c._db._conn() as conn:
        assert conn.execute('SELECT status FROM scans').fetchone()[0] == 'aborted'
    assert client.post('/api/scan/start', json={'return_s': 2}).status_code == 422
    started = client.post('/api/scan/start', json=profile).json()
    assert started['phase'] == 'INITIAL_DELAY'
    assert not c.automatic_ready()
    assert load_settings(tmp_path/'scan_settings.json').return_s == 14
    assert client.get('/api/scan/settings').json()['state']['run_id'] == started['run_id']
    runtime[0] += 5
    c.tick()
    assert c.capture_meta(2).position == 'lower'
    assert not c.accepts(meta)
    assert client.post('/api/scan/stop').status_code == 200
    monkeypatch.setattr(c, 'save_settings', Mock(side_effect=OSError('disk full')))
    assert client.post('/api/scan/start', json=profile).status_code == 503
    assert c.scan_state()['phase'] == 'IDLE'

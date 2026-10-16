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
    c._scan_sm.start()
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

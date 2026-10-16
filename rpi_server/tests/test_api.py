from fastapi import FastAPI
from fastapi.testclient import TestClient
import config
from api_extensions import register_extensions
from persistence import PersistenceDB
from snapshot_writer import SnapshotWriter
from scan_state import ScanStateMachine
import numpy as np

def test_routes_filters_errors_and_review(tmp_path):
    db = PersistenceDB(str(tmp_path/'farm.db'))
    tid = db.commit_scan(db.begin_scan(1,'upper'),1,'upper',[[5,5,20,20,'worm',.9]])[0]
    app = FastAPI()
    state = {'db':db}
    register_extensions(app,lambda: state)
    pairs = [(r.path,method) for r in app.routes for method in getattr(r,'methods',[])]
    assert len(pairs) == len(set(pairs))
    client = TestClient(app)
    assert client.get('/api/config').json()['cycle_time_secs'] == 333
    assert client.get('/api/config').json()['buzzer_pin'] == 26
    assert client.get('/api/logs?station=1').json()['total'] == 1
    assert client.get('/api/logs?station=2').json()['total'] == 0
    assert client.get('/api/logs?station=5').status_code == 422
    assert client.get('/api/logs?page=0').status_code == 422
    assert client.get('/api/logs?date_from=9&date_to=2').status_code == 422
    assert client.get('/api/precision').json() == {}
    assert client.post(f'/api/logs/{tid}/review').status_code == 200
    assert client.get('/api/precision').json() == {'worm':1}
    assert client.post(f'/api/logs/{tid}/false-positive').status_code == 200
    assert client.get('/api/logs?is_fp=1').json()['total'] == 1
    assert client.get('/api/precision').json() == {'worm':0}
    state.clear()
    for endpoint in ('logs','scan/state','cooldowns','sms/status','precision'):
        assert client.get('/api/'+endpoint).status_code == 503
    assert client.post('/api/buzzer/mute',json={'muted':True}).status_code == 503

def test_snapshot_round_trip_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'SNAPSHOT_DIR',tmp_path/'snapshots')
    db = PersistenceDB(str(tmp_path/'farm.db'))
    tid = db.commit_scan(db.begin_scan(1,'upper'),1,'upper',[[5,5,20,20,'worm',.9]])[0]
    sm = ScanStateMachine(); sm.start()
    writer = SnapshotWriter(db)
    writer._write_snapshot(dict(frame=np.zeros((80,100,3),np.uint8),
        dets=[[5,5,20,20,'worm',.9]],track_id=tid,frame_meta=sm.make_frame_meta(1),ts=1000))
    relative = db.get_tracks_page()['items'][0]['thumb_path']
    assert relative == tid+'/latest_thumb.jpg'
    app = FastAPI(); register_extensions(app,lambda: {'db':db})
    client = TestClient(app)
    assert client.get('/api/snapshots/'+relative).headers['content-type'] == 'image/jpeg'
    (tmp_path/'secret.png').write_bytes(b'secret')
    assert client.get('/api/snapshots/%2e%2e%2fsecret.png').status_code == 404
    assert client.get('/api/snapshots/%2e%2e%5csecret.png').status_code == 404

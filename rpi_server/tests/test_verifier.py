import numpy as np
import cv2
import config
from verifier import DetectionVerifier, _CentroidTracker, _skin_ratio, _green_ratio
from scan_state import ScanStateMachine

def test_confidence_shape_and_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'REJECTED_DIR',tmp_path)
    verifier = DetectionVerifier()
    frame = np.zeros((100,100,3),np.uint8)
    meta = ScanStateMachine().make_frame_meta(1)
    def gate(box, name='worm', conf=.99, suppress=False):
        return verifier._check_gates(*box,name,conf,frame,100,100,suppress,meta)
    assert gate((10,10,30,30),conf=.01).startswith('conf')
    assert gate((10,10,11,11)).startswith('area')
    assert gate((10,10,30,30),suppress=True) == 'hand_in_frame'
    assert gate((10,10,30,30),name='whiteflies').startswith('green')
    assert gate((10,10,90,11)).startswith('ar')
    green = np.full((20,20,3),(0,200,0),np.uint8)
    assert _green_ratio(green) > .9
    skin = cv2.cvtColor(np.full((20,20,3),(10,110,200),np.uint8),cv2.COLOR_HSV2BGR)
    assert _skin_ratio(skin) > .9

def test_temporal_gate_never_replays_absent_or_drifting_candidates(monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(config,'TRACK_K',2)
    monkeypatch.setattr(config,'T_MIN_SECS',100)
    tracker = _CentroidTracker()
    meta = ScanStateMachine().make_frame_meta(1,100)
    det = [10,10,30,30,'worm',.99]
    assert tracker.tick([det],meta) == []
    assert len(tracker.tick([det],replace(meta,captured_at=100.1,seq=2))) == 1
    assert tracker.tick([],replace(meta,captured_at=100.2,seq=3)) == []
    assert tracker.tick([[25,10,45,30,'worm',.99]],replace(meta,captured_at=100.201,seq=4)) == []

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

def test_worm_skin_color_is_not_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    verifier = DetectionVerifier()
    skin_bgr = cv2.cvtColor(np.full((100, 100, 3), (10, 110, 200), np.uint8), cv2.COLOR_HSV2BGR)
    meta = ScanStateMachine().make_frame_meta(1)

    # Whitefly crop with skin tone is rejected
    wf_res = verifier._check_gates(10, 10, 30, 30, 'whiteflies', 0.99, skin_bgr, 100, 100, False, meta)
    assert wf_res is not None

    # Worm crop with skin tone must pass all gates
    worm_res = verifier._check_gates(10, 10, 30, 30, 'worm', 0.99, skin_bgr, 100, 100, False, meta)
    assert worm_res is None, f"Worm was unexpectedly rejected: {worm_res}"


def _leaf(size=200):
    # Lettuce-like lime green in BGR (hue ~39, high saturation).
    return np.full((size, size, 3), (60, 220, 170), np.uint8)


def test_fungus_on_a_worm_is_dropped_even_for_weak_worms(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    monkeypatch.setattr(config, 'FUNGUS_MIN_PER_FRAME', 1)    # one spot: only the worm rule applies
    verifier = DetectionVerifier()
    meta = ScanStateMachine().make_frame_meta(1)
    fungus = [50, 50, 120, 120, 'fungus', .9]
    weak_worm = [60, 60, 100, 80, 'worm', .30]   # below counted 0.45, above hint 0.25
    assert verifier.filter_static([fungus], _leaf(), meta) == [fungus]
    assert verifier.filter_static([fungus, weak_worm], _leaf(), meta) == []
    reasons = [reason for _, reason in verifier.explain([fungus, weak_worm], _leaf(), meta)]
    assert reasons[0] == 'worm_overlap' and reasons[1].startswith('conf')


def test_fungus_on_dark_background_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    verifier = DetectionVerifier()
    meta = ScanStateMachine().make_frame_meta(1)
    dark = np.full((200, 200, 3), 20, np.uint8)
    reason = verifier._check_gates(50, 50, 120, 120, 'fungus', .9, dark, 200, 200, False, meta)
    assert reason.startswith('leaf')
    assert verifier._check_gates(50, 50, 120, 120, 'fungus', .9, _leaf(), 200, 200, False, meta) is None


def test_small_pale_whitefly_on_leaf_passes_but_huge_box_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    verifier = DetectionVerifier()
    meta = ScanStateMachine().make_frame_meta(1)
    frame = _leaf()
    frame[100:108, 100:108] = (240, 240, 240)
    # Mostly-leaf box around a small white insect: the old mean V/S test failed this.
    assert verifier._check_gates(98, 98, 110, 110, 'whiteflies', .9, frame, 200, 200, False, meta) is None
    assert verifier._check_gates(40, 40, 140, 140, 'whiteflies', .9, frame, 200, 200, False, meta).startswith('area')


def test_fungus_on_glare_or_casing_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    verifier = DetectionVerifier()
    meta = ScanStateMachine().make_frame_meta(1)
    frame = _leaf()
    frame[60:100, 60:100] = (250, 250, 250)          # white glare spot / droplet on the leaf
    assert verifier._check_gates(60, 60, 100, 100, 'fungus', .9, frame, 200, 200, False, meta).startswith('glare')
    casing = _leaf()
    casing[100:, :] = (200, 205, 200)                # dull white casing below the leaf
    # Box on the casing right at the leaf edge: leaf in the ring, none inside.
    assert verifier._check_gates(60, 102, 100, 122, 'fungus', .9, casing, 200, 200, False, meta).startswith('offleaf')
    assert verifier._check_gates(60, 140, 100, 180, 'fungus', .9, casing, 200, 200, False, meta).startswith('leaf')
    lesion = _leaf()
    lesion[70:90, 70:90] = (30, 70, 140)             # brown lesion on the leaf
    assert verifier._check_gates(60, 60, 100, 100, 'fungus', .9, lesion, 200, 200, False, meta) is None


def test_whitefly_speck_no_brighter_than_leaf_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    verifier = DetectionVerifier()
    meta = ScanStateMachine().make_frame_meta(1)
    frame = _leaf()
    frame[100:108, 100:108] = (215, 215, 215)        # pale but not brighter than the leaf
    assert verifier._check_gates(98, 98, 110, 110, 'whiteflies', .9, frame, 200, 200, False, meta).startswith('contrast')


def test_reddish_background_is_not_a_hand_but_a_skin_blob_is(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'REJECTED_DIR', tmp_path)
    from verifier import _skin_blob_ratio
    skin = cv2.cvtColor(np.full((1, 1, 3), (10, 110, 200), np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    scattered = _leaf(400)
    scattered[::7, ::7] = skin                       # speckled skin-tone pixels, no blob
    assert _skin_blob_ratio(scattered, [0, 0, 1, 1]) < config.SKIN_FRAME_MAX_PCT
    hand = _leaf(400)
    hand[100:300, 100:300] = skin                    # one solid hand-sized blob
    assert _skin_blob_ratio(hand, [0, 0, 1, 1]) > config.SKIN_FRAME_MAX_PCT
    wall = _leaf(400)
    wall[:200, :] = (40, 20, 90)                     # dark red wall behind the lettuce (Y~40)
    assert _skin_blob_ratio(wall, [0, 0, 1, 1]) < config.SKIN_FRAME_MAX_PCT


def test_tracker_confirms_by_frames_on_a_slow_loop(monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(config, 'TRACK_K', 3)
    monkeypatch.setattr(config, 'T_MIN_SECS', 100)
    tracker = _CentroidTracker()
    meta = ScanStateMachine().make_frame_meta(1, 100)
    det = [10, 10, 30, 30, 'whiteflies', .9]
    # One processed frame per second: 3 sightings never fit in the old 0.75 s window.
    assert tracker.tick([det], meta) == []
    assert tracker.tick([det], replace(meta, captured_at=101, seq=2)) == []
    assert len(tracker.tick([det], replace(meta, captured_at=102, seq=3))) == 1

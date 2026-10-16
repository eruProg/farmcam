from scan_state import ScanStateMachine, ScanPhase

def test_full_cycle_and_return_trip():
    now = [100.0]
    sm = ScanStateMachine(time_fn=lambda: now[0])
    sm.start()
    moves = []
    for _ in range(28):
        assert sm.is_detecting == (sm.phase in (ScanPhase.DETECT_UPPER, ScanPhase.DETECT_LOWER))
        if sm.phase == ScanPhase.MOVING_TO_STATION:
            moves.append((sm.station, sm.phase_duration))
        now[0] += sm.phase_duration
        sm.tick()
    assert now[0] == 433
    assert moves == [(2,10), (3,10), (4,10), (1,15)]
    assert sm.station == 1 and sm.phase == ScanPhase.DETECT_UPPER

def test_metadata_is_immutable_and_capture_time_preserved():
    from dataclasses import FrozenInstanceError
    import pytest
    sm = ScanStateMachine(time_fn=lambda: 100)
    sm.start()
    meta = sm.make_frame_meta(9, 101)
    assert meta.captured_at == 101 and meta.station == 1
    with pytest.raises(FrozenInstanceError):
        meta.station = 2

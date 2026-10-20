from dataclasses import FrozenInstanceError
import pytest
from scan_state import ScanStateMachine, ScanPhase
from scan_settings import ScanSettings, load_settings, save_settings


def test_two_full_cycles():
    now = [100.0]
    sm = ScanStateMachine(time_fn=lambda: now[0])
    assert sm.phase is ScanPhase.IDLE
    sm.start(ScanSettings(return_s=12.3))
    assert sm.phase is ScanPhase.INITIAL_DELAY and sm.phase_duration == 5
    now[0] += 5
    sm.tick()
    expected = [
        (1, 'DETECT_LOWER', 60, 'lower'), (1, 'TRANSIT_TO_UPPER', 2.8, 'lower'),
        (1, 'DETECT_UPPER', 60, 'upper'), (2, 'MOVING_TO_STATION', 8.5, 'upper'),
        (2, 'DETECT_UPPER', 60, 'upper'), (2, 'TRANSIT_TO_LOWER', 2.8, 'upper'),
        (2, 'DETECT_LOWER', 60, 'lower'), (3, 'MOVING_TO_STATION', 8.6, 'lower'),
        (3, 'DETECT_LOWER', 60, 'lower'), (3, 'TRANSIT_TO_UPPER', 2.8, 'lower'),
        (3, 'DETECT_UPPER', 60, 'upper'), (4, 'MOVING_TO_STATION', 8.6, 'upper'),
        (4, 'DETECT_UPPER', 60, 'upper'), (4, 'TRANSIT_TO_LOWER', 2.8, 'upper'),
        (4, 'DETECT_LOWER', 60, 'lower'), (1, 'RETURNING', 12.3, 'lower'),
    ]
    for _ in range(2):
        for station, phase, duration, position in expected:
            assert (sm.station, sm.phase.name, sm.phase_duration, sm.position) == (station, phase, duration, position)
            assert sm.is_detecting == phase.startswith('DETECT')
            now[0] = sm.phase_started_at + duration - .001
            assert sm.tick() is None
            now[0] = sm.phase_started_at + duration
            sm.tick()
    assert sm.phase is ScanPhase.DETECT_LOWER and sm.station == 1


def test_start_stop_metadata_and_settings(tmp_path):
    settings = ScanSettings(return_s=9, initial_delay_s=0)
    sm = ScanStateMachine(time_fn=lambda: 100)
    with pytest.raises(ValueError):
        sm.start()
    sm.start(settings)
    old_run = sm.run_id
    meta = sm.make_frame_meta(9, 101)
    assert meta.captured_at == 101 and meta.position == 'lower'
    with pytest.raises(FrozenInstanceError):
        meta.station = 2
    with pytest.raises(RuntimeError):
        sm.start(settings)
    sm.stop()
    assert not sm.is_detecting
    sm.start(settings)
    assert sm.run_id != old_run
    path = tmp_path / 'settings.json'
    save_settings(path, settings)
    assert load_settings(path) == settings
    restarted = ScanStateMachine()
    restarted.settings = load_settings(path)
    assert restarted.phase is ScanPhase.IDLE
    path.write_text('{bad')
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize('value', [None, True, '5', 0, -1, float('inf'), float('nan')])
def test_invalid_return(value):
    with pytest.raises(ValueError):
        ScanSettings.parse(ScanSettings(return_s=value).to_dict())


def test_complete_profile_required():
    with pytest.raises(ValueError):
        ScanSettings.parse({'return_s': 5})


def test_late_tick_does_not_accumulate_timing_drift():
    now = [100.0]
    sm = ScanStateMachine(time_fn=lambda: now[0])
    sm.start(ScanSettings(return_s=10, initial_delay_s=0))
    now[0] = 160.7
    sm.tick()
    assert sm.phase is ScanPhase.TRANSIT_TO_UPPER
    assert sm.remaining == pytest.approx(2.1)
    now[0] = 163.2
    sm.tick()
    assert sm.phase_started_at == pytest.approx(162.8)

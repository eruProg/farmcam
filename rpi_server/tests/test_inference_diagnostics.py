import ast
from pathlib import Path
from types import SimpleNamespace

from inference_diagnostics import InferenceDiagnostics, model_identity
from compare_inference import inference_scope
from scan_state import ScanPhase


def test_diagnostics_are_fresh_attributed_and_not_changed_by_later_inference():
    clock = [10]
    diagnostics = InferenceDiagnostics(lambda:clock[0], lambda:100)
    meta = SimpleNamespace(scan_id='window-1', station=1, phase=ScanPhase.DETECT_LOWER, captured_at=9)
    before = [[0, 0, 10, 10, 'worm', .8]]
    debug = {'class_scores': {'DET': {'worm': .8}}}
    scan = dict(scan_id='window-1', station=1, phase='DETECT_LOWER')
    assert diagnostics.snapshot(scan)['last_inference'] is None
    diagnostics.record(42, meta, 9, before, [], debug)
    debug['class_scores']['DET']['worm'] = 0
    snapshot = diagnostics.snapshot(scan)
    assert snapshot['sample_matches_scan']
    sample = snapshot['last_inference']
    assert sample['frame_sequence'] == 42 and sample['duration_s'] == 1
    assert sample['before_verification'] == {'worm': 1}
    assert sample['after_verification'] == {}
    assert sample['class_scores']['DET']['worm'] == .8
    clock[0] = 20
    for phase in ('IDLE', 'MOVING_TO_STATION', 'RETURNING'):
        state = diagnostics.snapshot(dict(scan, phase=phase))
        assert not state['sample_matches_scan']
        assert state['last_inference_age_s'] == 10
    diagnostics.record(43, meta, 19, before, None, debug)
    assert diagnostics.snapshot(scan)['last_inference']['result'] == 'discarded_by_coordinator'


def test_model_identity_hashes_actual_bytes(tmp_path):
    path = tmp_path / 'model.hef'
    path.write_bytes(b'abc')
    identity = model_identity(dict(label='DET', path=path, class_names=['whiteflies', 'worm']))
    assert identity['sha256'] == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'


def test_comparison_only_extracts_inference_and_resets_scope():
    source = Path(__file__).parents[1] / 'main.py'
    a = inference_scope(source, {}, True)
    b = inference_scope(source, {}, True)
    assert 'startup' not in a and 'coordinator' not in a and 'esp32_cmd' not in a
    assert a['temporal'] is not b['temporal']
    assert a['_function_hashes'] == b['_function_hashes']
    assert a['PER_CLASS_THRESHOLDS']['worm'] == .45
    assert a['_inference_regions'](1280, 960)


def test_loader_rejects_missing_pair_and_never_attempts_combined():
    tree = ast.parse((Path(__file__).parents[1] / 'main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if getattr(n, 'name', '') == 'init_hailo_models')
    calls = []
    scope = dict(HAS_HAILO=True, hailo_release_device=lambda:None,
                 _discover_hefs=lambda:[('SEG', 'seg', [])],
                 hailo_load_labels=lambda labels:calls.append(labels) or False)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'loader', 'exec'), scope)
    import pytest
    with pytest.raises(RuntimeError, match='Both'):
        scope['init_hailo_models']()
    assert calls == []
    scope['_discover_hefs'] = lambda:[('SEG', 'seg', []), ('DET', 'det', [])]
    with pytest.raises(RuntimeError, match='no combined'):
        scope['init_hailo_models']()
    assert calls == [['DET', 'SEG']]

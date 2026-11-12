import importlib

import config


def _config_with(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('FARMCAM_PEST_MODEL', raising=False)
    else:
        monkeypatch.setenv('FARMCAM_PEST_MODEL', value)
    return importlib.reload(config)


def test_whitefly_minimum_follows_installed_pest_model(monkeypatch):
    try:
        monkeypatch.delenv('FARMCAM_FUNGUS_MODEL', raising=False)
        # No service settings (server files copied by hand): the shipped models.
        default = _config_with(monkeypatch, None)
        assert default.PEST_MODEL == 'natural' and default.FUNGUS_MODEL == 'natural2'
        assert default.PER_CLASS_THRESHOLDS['whiteflies'] == default.PER_CLASS_THRESHOLDS['whitefly'] == .50
        assert default.PER_CLASS_THRESHOLDS['worm'] == .65
        assert default.SEG_CLASS_NAMES == ['fungus', 'dirt']
        assert default.PER_CLASS_THRESHOLDS['fungus'] == .30 and default.FUNGUS_MIN_PER_FRAME == 2
        old = _config_with(monkeypatch, 'old')
        assert old.PER_CLASS_THRESHOLDS['whiteflies'] == old.PER_CLASS_THRESHOLDS['whitefly'] == .65
        monkeypatch.setenv('FARMCAM_FUNGUS_MODEL', 'old')
        both_old = importlib.reload(config)
        assert both_old.PER_CLASS_THRESHOLDS['fungus'] == .40 and both_old.FUNGUS_MIN_PER_FRAME == 1
        monkeypatch.delenv('FARMCAM_FUNGUS_MODEL')
    finally:
        _config_with(monkeypatch, None)


def test_machine_scale_drops_close_up_whiteflies_only():
    import evaluate
    gts = [('whiteflies', 0, 0, 10, 8),          # machine size (~1.5 mm)
           ('whiteflies', 0, 0, 120, 90),        # internet close-up
           ('worm', 0, 0, 180, 40)]              # worms are big anyway
    real, impossible = evaluate.split_machine_scale(gts)
    assert [g[3] for g in real] == [10, 180]
    assert impossible == [('whiteflies', 0, 0, 120, 90)]


def test_machine_scale_drops_lesions_below_the_pi_size_gate():
    import evaluate
    gts = [('fungus', 0, 0, 6, 6), ('fungus', 0, 0, 10, 10)]   # 36 px2 < 50 px2 gate; 100 px2 counted
    real, impossible = evaluate.split_machine_scale(gts)
    assert real == [('fungus', 0, 0, 10, 10)] and impossible == [('fungus', 0, 0, 6, 6)]


def test_fungus_severity_matches_the_pi():
    import evaluate
    assert [evaluate.fungus_severity(p) for p in (0, 0.5, 5.0, 14.9, 15.0)] == \
        ['none', 'low', 'medium', 'medium', 'high']
    # Mask pixels win over the box area; 1280x960 frame.
    kept = [[0, 0, 100, 100, 'fungus', .9, None, 61440], [0, 0, 10, 10, 'fungus', .9, None, 0]]
    assert abs(evaluate.predicted_coverage(kept, 1280, 960) - (61440 + 100) * 100 / (1280 * 960)) < 1e-9


def test_label_coverage_from_outlines(tmp_path):
    import evaluate
    label = tmp_path / 'a.txt'
    label.write_text('0 0.1 0.1 0.3 0.1 0.3 0.3 0.1 0.3\n0 0.5 0.5 0.1 0.1\n')   # 4% polygon + 1% box
    assert abs(evaluate.label_coverage(label, ['fungus'], 640, 480) - 5.0) < 1e-6


def _leaf_with_spots(*boxes):
    import numpy as np
    frame = np.zeros((200, 200, 3), np.uint8)
    frame[:] = (40, 160, 60)                         # leaf green (BGR)
    for x1, y1, x2, y2 in boxes:
        frame[y1:y2, x1:x2] = (30, 60, 120)          # brown lesion
    return frame


def test_one_lone_fungus_spot_is_not_reported(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from verifier import DetectionVerifier
    monkeypatch.setattr(config, 'FUNGUS_MIN_PER_FRAME', 2)
    verifier = DetectionVerifier(rejected_dir=tmp_path)
    meta = SimpleNamespace(station=None, position=None)
    one = [[45, 45, 65, 65, 'fungus', .9, None, 0]]
    two = one + [[120, 120, 140, 140, 'fungus', .9, None, 0]]
    assert [r for _, r in verifier.explain(one, _leaf_with_spots((50, 50, 60, 60)), meta)] == ['fungus_alone']
    frame = _leaf_with_spots((50, 50, 60, 60), (125, 125, 135, 135))
    assert [r for _, r in verifier.explain(two, frame, meta)] == [None, None]
    monkeypatch.setattr(config, 'FUNGUS_MIN_PER_FRAME', 1)
    assert [r for _, r in verifier.explain(one, _leaf_with_spots((50, 50, 60, 60)), meta)] == [None]


def test_evaluate_applies_the_frame_rule_after_the_minimum(monkeypatch):
    import evaluate
    monkeypatch.setattr(config, 'FUNGUS_MIN_PER_FRAME', 2)
    kept = [[0, 0, 9, 9, 'fungus', .9], [20, 20, 29, 29, 'fungus', .9]]
    assert evaluate.frame_rule(kept, 'fungus') == kept
    assert evaluate.frame_rule(kept[:1], 'fungus') == []
    assert evaluate.frame_rule(kept[:1], 'worm') == kept[:1]


def test_fungus_model_switch_changes_only_the_seg_pass(monkeypatch):
    import ast, types
    from pathlib import Path
    source = (Path(__file__).parent.parent / 'main.py').read_text(encoding='utf-8-sig')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', None) == 'SPLIT_COLOR_PASSES' for t in n.targets))
    passes = lambda pest, fungus: eval(compile(ast.Expression(node.value), 'main.py', 'eval'), {
        'config': types.SimpleNamespace(PEST_MODEL=pest, FUNGUS_MODEL=fungus,
                                        FUNGUS_NATURAL=fungus in ('natural', 'natural2'))})
    assert passes('natural', 'natural') == [('SEG', 'natural', None, False), ('DET', 'natural', None, False)]
    assert passes('natural', 'old')[0] == ('SEG', 'swapped', None, False)
    assert passes('old', 'natural')[1:] == passes('old', 'old')[1:]

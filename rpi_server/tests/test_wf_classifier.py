"""Two-stage whitefly detection: YOLO candidates, then a CNN on a sharp crop
replaces the colour rules. Without the model file nothing changes."""
from types import SimpleNamespace

import numpy as np

import config
import wf_classifier as wc
from verifier import DetectionVerifier


def test_crop_window_is_the_same_spot_at_both_resolutions():
    lores = np.zeros((960, 1280, 3), np.uint8)
    main = np.zeros((3040, 4056, 3), np.uint8)
    lores[475:485, 635:645] = 255                      # a 10 px white speck
    main[1504:1536, 2012:2044] = 255                   # the same speck, ~3.17x bigger
    box = (635, 475, 645, 485)
    a, b = wc.crop_window(box, lores), wc.crop_window(box, main)
    assert a.shape == b.shape == (wc.CROP, wc.CROP, 3)
    assert a[48, 48].mean() > 200 and b[48, 48].mean() > 200      # centred on the speck
    assert a[5, 5].mean() == 0 and b[5, 5].mean() == 0


def test_crop_window_stays_inside_the_frame():
    frame = np.arange(960 * 1280, dtype=np.uint32).reshape(960, 1280) % 251
    frame = np.dstack([frame] * 3).astype(np.uint8)
    corner = wc.crop_window((0, 0, 6, 6), frame)
    assert corner.shape == (wc.CROP, wc.CROP, 3)
    assert np.array_equal(corner, wc.crop_window((0, 0, 6, 6), frame))   # deterministic, no padding


def test_missing_model_means_no_classifier(tmp_path):
    assert wc.load(tmp_path / 'none.onnx') is None
    broken = tmp_path / 'broken.onnx'
    broken.write_bytes(b'not a model')
    assert wc.load(broken) is None


class FakeClassifier:
    def __init__(self, p):
        self.p, self.seen = p, []

    def scores(self, crops):
        self.seen.append(len(crops))
        return [self.p] * len(crops)


def _leaf():
    return np.full((960, 1280, 3), (60, 220, 170), np.uint8)    # plain green: colour rules say "no"


def _meta():
    return SimpleNamespace(station=None, position=None)


def test_cnn_decides_whiteflies_instead_of_the_colour_rules(tmp_path):
    weak = [600, 400, 608, 408, 'whiteflies', 0.30]              # below the colour-rule minimum (0.50)
    plain = DetectionVerifier(rejected_dir=tmp_path)
    assert plain.explain([weak], _leaf(), _meta())[0][1].startswith('conf')
    yes = DetectionVerifier(rejected_dir=tmp_path, classifier=FakeClassifier(0.9))
    assert yes.explain([weak], _leaf(), _meta())[0][1] is None             # no pale/green checks
    no = DetectionVerifier(rejected_dir=tmp_path, classifier=FakeClassifier(0.1))
    assert no.explain([weak], _leaf(), _meta())[0][1] == 'cnn_0.10'
    too_weak = [600, 400, 608, 408, 'whiteflies', config.WF_STAGE1_MIN - 0.05]
    assert yes.explain([too_weak], _leaf(), _meta())[0][1].startswith('conf')


def test_all_whiteflies_of_a_frame_are_zoomed_and_classified_together(tmp_path):
    asked = []
    clf = FakeClassifier(0.9)
    verifier = DetectionVerifier(rejected_dir=tmp_path, classifier=clf,
                                 zoom=lambda boxes: asked.append(boxes) or [np.zeros((96, 96, 3), np.uint8)] * len(boxes))
    dets = [[100 + 40 * i, 400, 108 + 40 * i, 408, 'whiteflies', 0.6] for i in range(3)]
    worm = [700, 300, 800, 330, 'worm', 0.9]
    explained = verifier.explain(dets + [worm], _leaf(), _meta())
    assert len(asked) == 1 and len(asked[0]) == 3 and clf.seen == [3]
    assert [r for _, r in explained[:3]] == [None] * 3


def test_camera_zoom_cuts_the_main_frame_at_the_lores_spot():
    import ast, threading
    from pathlib import Path
    source = ast.parse((Path(__file__).parent.parent / 'main.py').read_text(encoding='utf-8-sig'))
    keep = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == '_serve_zoom']
    main_frame = np.zeros((3040, 4056, 3), np.uint8)
    main_frame[1504:1536, 2012:2044] = 255                        # speck at lores (635-645, 475-485)
    ns = dict(camera=SimpleNamespace(capture_array=lambda stream: main_frame if stream == 'main' else None))
    exec(compile(ast.Module(body=keep, type_ignores=[]), 'main.py', 'exec'), ns)
    request = {'boxes': [(635, 475, 645, 485)], 'done': threading.Event(), 'crops': None}
    ns['_serve_zoom'](request)
    assert request['done'].is_set() and request['crops'][0][48, 48].mean() > 200


def test_zoom_failure_falls_back_to_the_frame(tmp_path):
    def broken(boxes):
        raise RuntimeError('camera busy')
    verifier = DetectionVerifier(rejected_dir=tmp_path, classifier=FakeClassifier(0.9), zoom=broken)
    assert verifier.explain([[600, 400, 608, 408, 'whiteflies', 0.6]], _leaf(), _meta())[0][1] is None

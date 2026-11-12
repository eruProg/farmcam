import importlib
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import yaml

import config
import machine_tiles as mt


def test_pieces_are_the_pis_own_regions():
    regions = mt.pi_regions()
    assert regions[0] == (0, 0, 1280, 960) and len(regions) == 5
    assert all(r[2] - r[0] == 704 for r in regions[1:])


def test_clip_polygon_keeps_the_inside_part():
    square = [(-10, -10), (10, -10), (10, 10), (-10, 10)]
    clipped = mt.clip_polygon(square, (0, 0, 100, 100))
    assert abs(mt._area(clipped) - 100) < 1e-6


def test_whitefly_near_a_crop_edge_lands_in_both_overlapping_crops():
    whitefly = [(1, [(600, 100), (606, 100), (606, 106), (600, 106)], True)]
    left = mt.piece_labels(whitefly, (0, 0, 704, 704), as_boxes=True)
    right = mt.piece_labels(whitefly, (576, 0, 1280, 704), as_boxes=True)
    assert len(left) == len(right) == 1
    cls, cx, cy, w, h = left[0].split()
    assert cls == '1' and abs(float(cx) * 704 - 603) < 0.01 and abs(float(w) * 704 - 6) < 0.01
    assert abs(float(right[0].split()[1]) * 704 - 27) < 0.01


def test_a_box_mostly_outside_a_piece_is_dropped():
    box = [(0, [(700, 10), (720, 10), (720, 30), (700, 30)], True)]       # 20% inside the left crop
    assert mt.piece_labels(box, (0, 0, 704, 704), as_boxes=True) == []


def _export(root, names, labels_by_photo):
    for split in ('train',):
        (root / split / 'images').mkdir(parents=True)
        (root / split / 'labels').mkdir(parents=True)
    (root / 'data.yaml').write_text(yaml.safe_dump({'nc': len(names), 'names': names}))
    for name, lines in labels_by_photo.items():
        cv2.imwrite(str(root / 'train' / 'images' / f'{name}.rf.abc.jpg'), np.full((3040, 4056, 3), 90, np.uint8))
        (root / 'train' / 'labels' / f'{name}.rf.abc.txt').write_text('\n'.join(lines))


def test_machine_set_split_by_burst_cut_and_kept_whole_for_test(tmp_path):
    photos = {f'field_full_20261027_{10 + i:02d}0000_000000_jpg':
              (['0 0.5 0.5 0.003 0.004'] if i % 2 == 0 else []) for i in range(20)}
    _export(tmp_path / 'export', ['whitefly', 'worm'], photos)
    stats = mt.add_machine_set(tmp_path / 'export', tmp_path / 'pest', tmp_path / 'machine_test',
                               ['whitefly', 'worm'], as_boxes=True)
    assert stats['train photos'] + stats['valid photos'] + stats['test photos'] == 20
    assert stats['test photos'] >= 2 and stats['valid photos'] >= 2
    whole = list((tmp_path / 'machine_test' / 'test' / 'images').glob('*.jpg'))
    assert len(whole) == stats['test photos'] and cv2.imread(str(whole[0])).shape == (960, 1280, 3)
    pieces = list((tmp_path / 'pest' / 'train' / 'images').glob('*.jpg'))
    assert {cv2.imread(str(p)).shape[:2] for p in pieces} <= {(960, 1280), (704, 704)}
    labelled = [p for p in (tmp_path / 'pest' / 'train' / 'labels').glob('*.txt') if p.read_text().strip()]
    assert len(pieces) - len(labelled) <= mt.NEG_PER_POSITIVE * len(labelled)   # negatives capped


def test_fungus_and_dirt_classes_are_remapped(tmp_path):
    # Roboflow sorts class names: dirt=0, fungus=1 in the export.
    _export(tmp_path / 'export', ['dirt', 'fungus'],
            {'field_full_20261027_100000_000000_jpg': ['0 0.5 0.5 0.01 0.01', '1 0.2 0.2 0.3 0.2 0.3 0.3 0.2 0.3']})
    mt.SHARES, saved = {'train': 1.0, 'valid': 0.0, 'test': 0.0}, mt.SHARES
    try:
        mt.add_machine_set(tmp_path / 'export', tmp_path / 'fungus', tmp_path / 't', ['fungus', 'dirt'], as_boxes=False)
    finally:
        mt.SHARES = saved
    full = (tmp_path / 'fungus' / 'train' / 'labels').glob('*_r0.txt')
    classes = sorted(line.split()[0] for line in next(full).read_text().splitlines())
    assert classes == ['0', '1']     # fungus -> 0, dirt -> 1


def test_shrunk_export_uses_the_full_size_original_and_skips_other_photos(tmp_path):
    export = tmp_path / 'export'
    _export(export, ['cup', 'dirt', 'fungus'], {})
    name = 'field_full_20261027_100000_000000'
    small = np.full((479, 640, 3), 90, np.uint8)
    cv2.imwrite(str(export / 'train' / 'images' / f'{name}_jpg.rf.abc.jpg'), small)
    (export / 'train' / 'labels' / f'{name}_jpg.rf.abc.txt').write_text('1 0.5 0.5 0.6 0.5 0.6 0.6 0.5 0.6\n0 0.1 0.1 0.2 0.1 0.2 0.2\n')
    cv2.imwrite(str(export / 'train' / 'images' / 'IMG2026_jpg.rf.def.jpg'), small)   # an old phone photo
    originals = tmp_path / 'originals'
    originals.mkdir()
    full = np.full((3040, 4056, 3), 90, np.uint8)
    full[1520:1824, 2028:2433] = 255                        # the dirt spot, only sharp in the original
    cv2.imwrite(str(originals / f'{name}.jpg'), full)
    mt.SHARES, saved = {'train': 1.0, 'valid': 0.0, 'test': 0.0}, mt.SHARES
    try:
        stats = mt.add_machine_set(export, tmp_path / 'fungus', tmp_path / 't', ['fungus', 'dirt'],
                                   as_boxes=False, originals=originals)
    finally:
        mt.SHARES = saved
    assert stats['train photos'] == 1 and stats['full-size originals used'] == 1
    assert not stats['photos smaller than the Pi frame (upscaled)']
    lines = (tmp_path / 'fungus' / 'train' / 'labels' / 'machine_field_full_20261027_100000_000000_r0.txt').read_text()
    assert [l.split()[0] for l in lines.splitlines()] == ['1']     # dirt kept as class 1, cup dropped
    frame = cv2.imread(str(tmp_path / 'fungus' / 'train' / 'images' / 'machine_field_full_20261027_100000_000000_r0.jpg'))
    assert frame[480 + 20, 640 + 20].mean() > 200               # the spot sits where the label says


def test_an_all_null_machine_set_still_adds_negatives(tmp_path):
    base = tmp_path / 'pest' / 'train'
    (base / 'labels').mkdir(parents=True)
    for i in range(40):                                      # the base set: 40 labelled photos
        (base / 'labels' / f'old{i}.txt').write_text('0 0.5 0.5 0.01 0.01\n')
    _export(tmp_path / 'export', ['whitefly', 'worm'],
            {f'field_full_20261027_{10 + i:02d}0000_000000_jpg': [] for i in range(8)})
    mt.SHARES, saved = {'train': 1.0, 'valid': 0.0, 'test': 0.0}, mt.SHARES
    try:
        stats = mt.add_machine_set(tmp_path / 'export', tmp_path / 'pest', tmp_path / 't',
                                   ['whitefly', 'worm'], as_boxes=True)
    finally:
        mt.SHARES = saved
    assert stats['train empty pieces'] == int(mt.NEG_SHARE_OF_SET * 40)    # 10, not 2


def test_reviewed_whiteflies_are_added_to_a_photo_marked_null(tmp_path):
    name = 'field_full_20261027_100000_000000'
    _export(tmp_path / 'export', ['whitefly', 'worm'], {f'{name}_jpg': []})
    extra = {name: [(0, [(600, 400), (608, 400), (608, 408), (600, 408)], True)]}
    mt.SHARES, saved = {'train': 1.0, 'valid': 0.0, 'test': 0.0}, mt.SHARES
    try:
        stats = mt.add_machine_set(tmp_path / 'export', tmp_path / 'pest', tmp_path / 't',
                                   ['whitefly', 'worm'], as_boxes=True, extra=extra)
    finally:
        mt.SHARES = saved
    assert stats['reviewed labels added'] == 1
    line = (tmp_path / 'pest' / 'train' / 'labels' / f'machine_{name}_r0.txt').read_text().split()
    assert line[0] == '0' and abs(float(line[1]) * 1280 - 604) < 0.01


def test_review_answers_match_the_same_spot():
    import make_wf_crops as mw
    reviewed = {'field_full_x': [((600.04, 400.0, 608.0, 408.46), True)]}
    assert mw.review_answer(reviewed, 'field_full_x', [600.4, 400.2, 608.3, 408.4]) is True
    assert mw.review_answer(reviewed, 'field_full_x', [700, 400, 708, 408]) is None
    assert mw.review_answer(reviewed, 'other', [600, 400, 608, 408]) is None


def test_dirt_is_never_counted(tmp_path):
    from verifier import DetectionVerifier
    frame = np.full((200, 200, 3), (40, 160, 60), np.uint8)
    verifier = DetectionVerifier(rejected_dir=tmp_path)
    meta = SimpleNamespace(station=None, position=None)
    reasons = [r for _, r in verifier.explain([[50, 50, 60, 60, 'dirt', .99, None, 0]], frame, meta)]
    assert reasons == ['dirt']


def test_worm_on_the_background_is_rejected(tmp_path, monkeypatch):
    from verifier import DetectionVerifier
    monkeypatch.setattr(config, 'WORM_RING_PLANT_MIN', 0.20)
    frame = np.full((400, 400, 3), 40, np.uint8)            # dark grey wall / pipe
    frame[:, 200:] = (40, 160, 60)                           # lettuce on the right
    verifier = DetectionVerifier(rejected_dir=tmp_path)
    meta = SimpleNamespace(station=None, position=None)
    on_wall = [20, 100, 120, 130, 'worm', .9, None, 0]
    on_leaf = [260, 100, 360, 130, 'worm', .9, None, 0]
    reasons = [r for _, r in verifier.explain([on_wall, on_leaf], frame, meta)]
    assert reasons[0].startswith('worm_offplant') and reasons[1] is None


def test_seg_classes_follow_the_fungus_model(monkeypatch):
    try:
        monkeypatch.setenv('FARMCAM_FUNGUS_MODEL', 'natural2')
        two = importlib.reload(config)
        assert two.SEG_CLASS_NAMES == ['fungus', 'dirt'] and two.FUNGUS_NATURAL and two.FUNGUS_MIN_PER_FRAME == 2
        monkeypatch.setenv('FARMCAM_FUNGUS_MODEL', 'natural')
        assert importlib.reload(config).SEG_CLASS_NAMES == ['fungus']
    finally:
        monkeypatch.delenv('FARMCAM_FUNGUS_MODEL', raising=False)
        importlib.reload(config)

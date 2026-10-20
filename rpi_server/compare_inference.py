"""Compare saved BGR images using inference-only definitions from two main.py files.

Never imports either application module or runs startup, camera, coordinator,
web routes, pump, SMS or database code. Stop farmcam before using the Hailo chip.
"""
import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import traceback
from collections import deque

import cv2
import numpy as np

from inference_diagnostics import model_identity


def inference_scope(path, hardware, tiles):
    source = Path(path).read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    definitions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    selected = set()

    def include(name):
        if name in selected:
            return
        selected.add(name)
        for node in ast.walk(definitions[name]):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in definitions:
                include(node.id)

    for root in ('detect_frame', '_configure_one_hef', 'TemporalConsensus'):
        include(root)
    # Fail rather than accidentally bringing application side effects into a
    # diagnostic run if a future inference function calls application logic.
    allowed = {'detect_frame', 'run_inference_on_network', 'postprocess_yolov8',
        'TemporalConsensus', 'nms', '_configure_one_hef', '_hef_input_hw',
        '_inference_regions', '_map_crop_detection', '_merge_region_detections',
        '_summarize_outputs', '_record_class_scores', '_decode_hailo_nms',
        '_decode_nms_tensor', '_valid_box', '_as_hwc', '_maybe_sigmoid',
        '_sigmoid', '_softmax', '_dfl_decode', '_mask_to_polygon_and_area'}
    if selected - allowed:
        raise ValueError(f'Unexpected inference dependencies: {sorted(selected - allowed)}')
    scope = dict(np=np, cv2=cv2, time=time, threading=threading,
                 traceback=traceback, deque=deque, **hardware)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                scope[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    scope.update(SMALL_OBJECT_TILES=tiles, hailo_swap_lock=threading.Lock(), hailo_models=[])
    scope['MIN_CONF_THRESHOLD'] = min(scope['PER_CLASS_THRESHOLDS'].values())
    for node in tree.body:
        if getattr(node, 'name', None) in selected:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    scope['temporal'] = scope['TemporalConsensus'](buffer_size=5, min_hits=3, iou_threshold=.3)
    scope['_source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    scope['_function_hashes'] = {name: hashlib.sha256(ast.dump(definitions[name],
        include_attributes=False).encode()).hexdigest() for name in sorted(selected)}
    return scope


def run_version(path, models, images, repeats, tiles, hardware):
    scope = inference_scope(path, hardware, tiles)
    device = hardware['VDevice']()
    scope['hailo_vdevice'] = device
    report = dict(source=str(Path(path).resolve()), sha256=scope['_source_sha256'],
                  functions=scope['_function_hashes'], models=[], images=[],
                  thresholds=scope['PER_CLASS_THRESHOLDS'], small_object_tiles=tiles)
    try:
        for label, filename, classes in [('SEG', 'best_seg.hef', scope['SEG_CLASS_NAMES']),
                                          ('DET', 'best_detect.hef', scope['DETECT_CLASS_NAMES'])]:
            entry = scope['_configure_one_hef'](label, models / filename, classes)
            if entry is None:
                raise RuntimeError(f'{label} failed to load for {path}')
            scope['hailo_models'].append(entry)
            report['models'].append(model_identity(entry))
        debug_template = copy.deepcopy(scope['_infer_debug'])
        for image_path in images:
            frame = cv2.imread(str(image_path))
            if frame is None:
                raise ValueError(f'Cannot read image: {image_path}')
            scope['temporal'].buffer.clear()
            scope['_infer_debug'] = copy.deepcopy(debug_template)
            frames = []
            for index in range(repeats):
                started = time.monotonic()
                detections = scope['detect_frame'](frame.copy())
                frames.append(dict(iteration=index + 1, duration_s=time.monotonic() - started,
                                   detections=detections, debug=copy.deepcopy(scope['_infer_debug'])))
            report['images'].append(dict(path=str(image_path.resolve()),
                sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(), frames=frames))
    finally:
        scope['hailo_models'].clear()
        device.release()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, required=True)
    parser.add_argument('--current', type=Path, default=Path(__file__).with_name('main.py'))
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True, help='Folder of saved PNG/JPEG images')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5, help='Identical frames per image for temporal confirmation')
    parser.add_argument('--no-tiles', action='store_true')
    args = parser.parse_args()
    if not 3 <= args.repeats <= 30:
        parser.error('--repeats must be between 3 and 30')
    images = sorted(p for p in args.images.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png'))
    if not images:
        parser.error('No PNG/JPEG images found')
    for path in [args.old, args.current, args.models/'best_seg.hef', args.models/'best_detect.hef']:
        if not path.is_file():
            parser.error(f'Missing file: {path}')
    # The file report is explicit; never overwrite a source/model/input image.
    if args.output.exists():
        parser.error('Output already exists; choose a new report filename')
    import hailo_platform
    hardware = {name: getattr(hailo_platform, name) for name in
        ('HEF', 'VDevice', 'HailoStreamInterface', 'InferVStreams', 'ConfigureParams',
         'InputVStreamParams', 'OutputVStreamParams', 'FormatType')}
    report = dict(repeated_still_images=True, note='Inference comparison, not a field accuracy measurement')
    try:
        for name, path in [('old', args.old), ('current', args.current)]:
            report[name] = run_version(path, args.models, images, args.repeats, not args.no_tiles, hardware)
        report['identical_functions'] = report['old']['functions'] == report['current']['functions']
    finally:
        args.output.write_text(json.dumps(report, indent=2, default=lambda x:
            x.item() if isinstance(x, np.generic) else x.tolist() if isinstance(x, np.ndarray) else str(x)), encoding='utf-8')
    print(f'Comparison saved to {args.output}')


if __name__ == '__main__':
    main()

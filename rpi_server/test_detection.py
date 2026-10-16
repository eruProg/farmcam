"""Hardware-free regression tests against definitions extracted from main.py.

Run: python -m unittest discover -s rpi_server -p 'test_*.py'
Requires numpy and OpenCV, like the server; never opens the camera or pump.
"""
import ast
from collections import deque
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import Mock

import cv2
import numpy as np


def definitions():
    source = ast.parse(Path(__file__).with_name("main.py").read_text(encoding="utf-8"))
    constants = {"CONFIDENCE_THRESHOLD", "NMS_IOU_THRESHOLD", "REG_MAX", "MIN_BOX_AREA",
                 "PER_CLASS_MIN_BOX_AREA", "MIN_MASK_AREA_PX", "MIN_MASK_FILL_RATIO",
                 "MAX_DETECTIONS_PER_FRAME", "MIN_ASPECT_RATIO", "MAX_ASPECT_RATIO",
                 "PER_CLASS_THRESHOLDS", "CLASS_NAMES", "SERVER_BUILD"}
    functions = {"_sigmoid", "_softmax", "_dfl_decode", "_valid_box", "_as_hwc",
                 "_maybe_sigmoid", "nms", "_summarize_outputs", "_decode_nms_tensor",
                 "_decode_hailo_nms", "postprocess_yolov8", "_mask_to_polygon_and_area",
                 "TemporalConsensus", "startup", "init_camera", "capture_frames",
                 "hailo_load_labels", "_record_class_scores", "_inference_regions",
                 "_map_crop_detection", "_merge_region_detections", "detect_frame"}
    nodes = [n for n in source.body if
             (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in functions) or
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants
                                              for t in n.targets))]
    ns = dict(np=np, cv2=cv2, deque=deque, _infer_debug={}, SMALL_OBJECT_TILES=True)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.ns = definitions()

    def raw_heads(self, names, candidates):
        boxes = np.full((1, 80, 80, 64), -20.0, dtype=np.float32)
        classes = np.zeros((1, 80, 80, len(names)), dtype=np.float32)
        for y, x, cls, score in candidates:
            # Four DFL sides of one grid cell (16x16 image box).
            for side in range(4):
                boxes[0, y, x, side * 16 + 1] = 20
            classes[0, y, x, cls] = score
        return {"box": boxes, "cls": classes}

    def test_single_fungus_channel_survives(self):
        for shape in [(1, 80, 80, 1), (80, 80, 1), (1, 1, 80, 80)]:
            self.assertEqual(self.ns["_as_hwc"](np.zeros(shape)).shape, (80, 80, 1))

    def test_fungus_below_general_threshold_is_retained(self):
        heads = self.raw_heads(["fungus"], [(20, 20, 0, 0.30)])
        result = self.ns["postprocess_yolov8"](heads, 640, 640, ["fungus"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][4], "fungus")

    def test_class_diagnostics_include_whitefly_even_when_worm_wins(self):
        heads = self.raw_heads(["whiteflies", "worm"], [(20, 20, 1, 0.9)])
        heads["cls"][0, 20, 20, 0] = 0.39
        self.ns["postprocess_yolov8"](heads, 640, 640, ["whiteflies", "worm"])
        self.assertAlmostEqual(self.ns["_infer_debug"]["class_max"]["whiteflies"], 0.39, places=5)

    def test_crops_cover_camera_frame_and_overlap(self):
        regions = self.ns["_inference_regions"](1280, 960)
        self.assertEqual(len(regions), 5)
        coverage = np.zeros((960, 1280), dtype=np.uint8)
        for x1, y1, x2, y2 in regions[1:]:
            self.assertEqual(x2 - x1, y2 - y1)
            coverage[y1:y2, x1:x2] += 1
        self.assertTrue(np.all(coverage > 0))
        self.assertTrue(np.any(coverage > 1))
        self.assertEqual(self.ns["_inference_regions"](640, 480), [(0, 0, 640, 480)])

    def test_crop_coordinates_polygon_and_mask_area(self):
        det = [0, 0, 640, 640, "fungus", 0.9, [[0, 0], [640, 640]], 409600]
        mapped = self.ns["_map_crop_detection"](det, (576, 256, 1280, 960), 1280, 960)
        np.testing.assert_allclose(mapped[:4], [288, 256 * 640 / 960, 640, 640])
        self.assertEqual(mapped[6][-1], [640, 640])
        self.assertEqual(mapped[7], int(409600 * 704 / 1280 * 704 / 960))

    def test_duplicate_crop_boxes_do_not_inflate_pest_count(self):
        det = [10, 10, 20, 20, "whiteflies", 0.9, None, 0]
        fungus = [10, 10, 20, 20, "fungus", 0.8, None, 0]
        merged = self.ns["_merge_region_detections"]([det, list(det), fungus])
        self.assertEqual(len(merged), 2)

    def test_all_three_classes_reach_live_results_after_confirmation(self):
        # Full-frame detections plus a whitefly visible only in a crop.
        def infer(frame, entry):
            if frame.shape[1] == 1280:
                return [[30, 30, 50, 50, "fungus", 0.8, None, 0],
                        [100, 100, 130, 110, "worm", 0.9, None, 0]]
            return [[100, 100, 106, 106, "whiteflies", 0.8, None, 0]]
        self.ns.update(hailo_swap_lock=threading.Lock(),
                       hailo_models=[{"label": "COMB", "class_names": self.ns["CLASS_NAMES"]}],
                       run_inference_on_network=infer,
                       temporal=self.ns["TemporalConsensus"]())
        frame = np.zeros((960, 1280, 3), dtype=np.uint8)
        self.assertEqual(self.ns["detect_frame"](frame), [])
        self.ns["detect_frame"](frame)
        result = self.ns["detect_frame"](frame)
        self.assertEqual({d[4] for d in result}, {"fungus", "whiteflies", "worm"})
        self.assertEqual(self.ns["_infer_debug"]["confirmed_boxes"]["worm"], 1)

    def test_fungus_below_class_threshold_is_rejected(self):
        heads = self.raw_heads(["fungus"], [(20, 20, 0, 0.20)])
        self.assertEqual(self.ns["postprocess_yolov8"](heads, 640, 640, ["fungus"]), [])

    def test_overlapping_different_classes_survive_nms(self):
        heads = self.raw_heads(["fungus", "whiteflies"], [(20, 20, 0, 0.8), (20, 21, 1, 0.9)])
        # Enlarge each side to two cells so IoU > 0.45.
        heads["box"][:] = -20
        for x in (20, 21):
            for side in range(4):
                heads["box"][0, 20, x, side * 16 + 2] = 20
        result = self.ns["postprocess_yolov8"](heads, 640, 640, ["fungus", "whiteflies"])
        self.assertEqual({d[4] for d in result}, {"fungus", "whiteflies"})

    def test_small_boxes_and_noise(self):
        valid = self.ns["_valid_box"]
        self.assertTrue(valid(10, 10, 16, 16, "whiteflies"))
        self.assertTrue(valid(10, 10, 16, 16, "fungus"))
        self.assertFalse(valid(10, 10, 16, 16, "worm"))
        self.assertFalse(valid(10, 10, 11, 11, "whiteflies"))
        self.assertFalse(valid(16, 16, 10, 10, "fungus"))

    def test_hailo_ragged_nms_preserves_class_and_yx_order(self):
        tensor = [[np.empty((0, 5)), np.array([[0.1, 0.2, 0.2, 0.4, 0.9]])]]
        result = self.ns["postprocess_yolov8"]({"nms": tensor}, 640, 640, ["whiteflies", "worm"])
        self.assertEqual(result[0][:5], [128, 64, 256, 128, "worm"])

    def test_hailo_single_class_single_box(self):
        rows = np.array([[0.1, 0.2, 0.2, 0.4, 0.9]])
        for tensor in ([[rows]], rows.T.reshape(1, 1, 5, 1)):
            result = self.ns["postprocess_yolov8"]({"nms": tensor}, 640, 640, ["fungus"])
            self.assertEqual(len(result), 1)

    def test_empty_nms_is_valid(self):
        self.assertEqual(self.ns["postprocess_yolov8"](
            {"nms": [[np.empty((0, 5))]]}, 640, 640, ["fungus"]), [])

    def test_single_legacy_xyxy_proposal(self):
        tensor = np.array([[[10, 10, 16, 16, 0.9, 0]]])
        result = self.ns["_decode_nms_tensor"](tensor, ["whiteflies"], 640, 640, 0.35)
        self.assertEqual(len(result), 1)

    def test_small_mask_with_positive_logits(self):
        proto = np.full((160, 160, 32), 0.1, dtype=np.float32)
        coeff = np.zeros(32, dtype=np.float32)
        coeff[0] = 1
        poly, pixels = self.ns["_mask_to_polygon_and_area"](proto, coeff, 40, 40, 48, 48, 640, 640)
        self.assertIsNotNone(poly)
        self.assertEqual(pixels, 64)

    def test_temporal_requires_actual_repeat_from_start(self):
        tracker = self.ns["TemporalConsensus"]()
        det = [10, 10, 16, 16, "whiteflies", 0.9]
        self.assertEqual(tracker.update([det]), [])
        self.assertEqual(tracker.update([det]), [det])

    def test_unknown_heads_report_failure(self):
        with self.assertRaisesRegex(ValueError, "Unsupported YOLO"):
            self.ns["postprocess_yolov8"]({"bad": np.zeros((1, 7, 17))}, 640, 640, ["fungus"])

    def test_camera_failure_aborts_before_model_or_pump_start(self):
        camera_start = Mock(side_effect=RuntimeError("busy"))
        models, pump = Mock(), Mock()
        self.ns.update(acquire_instance_lock=Mock(), init_camera=camera_start,
                       init_hailo_models=models, init_pump=pump)
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.ns["startup"]()
        models.assert_not_called()
        pump.assert_not_called()

    def test_no_models_aborts_before_pump_or_threads(self):
        pump = Mock()
        self.ns.update(acquire_instance_lock=Mock(), init_camera=Mock(),
                       init_hailo_models=Mock(), hailo_models=[], init_pump=pump)
        with self.assertRaisesRegex(RuntimeError, "No Hailo models"):
            self.ns["startup"]()
        pump.assert_not_called()

    def test_partial_split_is_not_success(self):
        release = Mock()
        self.ns.update(hailo_catalog=[("SEG", "seg", ["fungus"]), ("DET", "det", ["whiteflies", "worm"])],
                       hailo_swap_lock=threading.Lock(), hailo_release_device=release,
                       hailo_open_device=Mock(return_value=True),
                       _configure_one_hef=Mock(side_effect=[{"label": "SEG"}, None]))
        self.assertFalse(self.ns["hailo_load_labels"](["SEG", "DET"]))
        self.assertEqual(release.call_count, 2)

    def test_capture_preserves_bgr_and_counts_new_frame(self):
        event = threading.Event()
        frame = np.array([[[0, 0, 255]]], dtype=np.uint8)
        cam = Mock()
        def capture(_):
            event.set()
            return frame
        cam.capture_array.side_effect = capture
        self.ns.update(camera=cam, HAS_CAMERA=True, stop_event=event,
                       frame_lock=threading.Lock(), frame_sequence=0,
                       coordinator=types.SimpleNamespace(capture_meta=Mock(return_value='stamp')),
                       time=types.SimpleNamespace(monotonic=lambda: 100.0))
        self.ns["capture_frames"]()
        np.testing.assert_array_equal(self.ns["current_frame"], frame)
        self.assertEqual(self.ns["frame_sequence"], 1)
        self.assertEqual(self.ns['current_frame_meta'], 'stamp')


if __name__ == "__main__":
    unittest.main()

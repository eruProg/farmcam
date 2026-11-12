"""Run the COMPILED pest model (8-bit, as on the Hailo-8L) on a PC, through the
Pi's own detection code. Needs WSL2 Ubuntu with the Hailo Dataflow Compiler
venv (best na pt/setup_wsl_hailo.sh); it does not run on Windows Python.

The compiler's .har file (saved next to the .hef) holds the same quantized
weights the chip runs; Hailo's SDK_QUANTIZED emulator reproduces the chip's
8-bit arithmetic on the CPU. Everything around it is main.py's real code:
letterbox, colour order, the 5 SAHI regions, YOLOv8 post-processing, crop
merging and size checks (detect_frame), then verifier.py's checks. Only the
3-of-5-frames check is skipped: a photo is one frame.

    source ~/hailo/bin/activate
    python hef_emulator.py --evaluate ../datasets_fixed/pest --split test
    python hef_emulator.py --images ../datasets/to_label/field_test_clean

The web tester (hef_test_server.py) uses the same class.
"""
import argparse
import ast
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

# The compiled model is the retrained (natural-colour) one.
os.environ.setdefault("FARMCAM_PEST_MODEL", "natural")

import cv2                                                      # noqa: E402
import numpy as np                                              # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                                                   # noqa: E402
from colour_check import is_swapped                             # noqa: E402
from verifier import DetectionVerifier                          # noqa: E402

DEFAULT_HAR = Path.home() / "farmcam_compile" / "best na pt" / "best_detect.har"
DEFAULT_SEG_HAR = Path.home() / "farmcam_compile" / "best na pt" / "best_seg.har"
FRAME_W, FRAME_H = 1280, 960

_FUNCTIONS = {"_sigmoid", "_softmax", "_dfl_decode", "_valid_box", "_as_hwc", "_maybe_sigmoid",
              "nms", "_summarize_outputs", "_decode_nms_tensor", "_decode_hailo_nms",
              "postprocess_yolov8", "_mask_to_polygon_and_area", "_record_class_scores",
              "_inference_regions", "_map_crop_detection", "_merge_region_detections",
              "detect_frame", "_unletterbox_det", "_inference_passes"}
_CONSTANTS = {"CONFIDENCE_THRESHOLD", "NMS_IOU_THRESHOLD", "REG_MAX", "MIN_BOX_AREA",
              "PER_CLASS_MIN_BOX_AREA", "MIN_MASK_AREA_PX", "MIN_MASK_FILL_RATIO",
              "MAX_DETECTIONS_PER_FRAME", "MIN_ASPECT_RATIO", "MAX_ASPECT_RATIO",
              "PER_CLASS_THRESHOLDS", "CLASS_NAMES", "DETECT_CLASS_NAMES", "SEG_CLASS_NAMES", "MODEL_COLOR_ORDER",
              "SPLIT_COLOR_PASSES", "SMALL_OBJECT_TILES"}


class _NoTemporal:
    """A photo is a single frame: hand every box straight on."""
    def update(self, dets):
        return dets


def pi_definitions():
    """main.py's detection functions, without importing main.py."""
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in _FUNCTIONS)
             or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in _CONSTANTS
                                                   for t in n.targets))]
    ns = dict(np=np, cv2=cv2, os=os, config=config, _infer_debug={},
              hailo_swap_lock=threading.Lock(), temporal=_NoTemporal(), latest_detections=[])
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    missing = (_FUNCTIONS | _CONSTANTS) - set(ns)
    if missing:
        raise RuntimeError(f"main.py no longer defines {sorted(missing)}")
    return ns


def to_camera_frame(image):
    """Match the Pi's 1280x960 stream (same rule as offline_test.py)."""
    h, w = image.shape[:2]
    if (w, h) == (FRAME_W, FRAME_H):
        return image
    if abs(w / h - FRAME_W / FRAME_H) < 0.02:
        return cv2.resize(image, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)
    scale = FRAME_W / float(max(w, h))
    return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


class EmulatedPi:
    """DET (pest) always; SEG (fungus) too when its .har is given, so
    detect_frame runs both models exactly like the Pi (incl. the worm veto)."""

    def __init__(self, har=DEFAULT_HAR, seg_har=None):
        import tensorflow as tf
        self._tf = tf
        self.har = Path(har)
        self.seg_har = Path(seg_har) if seg_har else None
        self.ns = pi_definitions()
        models = [{"label": "DET", "class_names": self.ns["DETECT_CLASS_NAMES"]}]
        if self.seg_har:
            models.insert(0, {"label": "SEG", "class_names": self.ns["SEG_CLASS_NAMES"]})
        self.ns.update(run_inference_on_network=self._run_inference_on_network, hailo_models=models)
        self.verifier = DetectionVerifier(rejected_dir=HERE / "data" / "eval_unused")
        self._lock = threading.Lock()
        self._contexts = []
        self._predict = {"DET": self._load(self.har)}
        if self.seg_har:
            self._predict["SEG"] = self._load(self.seg_har)

    def _load(self, har):
        from hailo_sdk_client import ClientRunner, InferenceContext
        tf = self._tf
        runner = ClientRunner(har=str(har))
        manager = runner.infer_context(InferenceContext.SDK_QUANTIZED)
        ctx = manager.__enter__()
        self._contexts.append((runner, manager))           # keep both alive
        model = runner.get_keras_model(ctx)
        warm = np.zeros((1, 640, 640, 3), np.float32)
        model.build(tf.data.Dataset.from_tensor_slices((warm, np.zeros(1))).batch(1))
        jit = model.is_jit_compile_supported()
        model.compile(jit_compile=jit)
        predict = tf.function(lambda x: model(x, training=False), jit_compile=jit, reduce_retracing=True)
        started = time.time()
        predict(tf.constant(warm))
        print(f"Emulated Hailo-8L model ready ({Path(har).name}, warm-up {time.time() - started:.0f} s)",
              flush=True)
        return predict

    def _run_inference_on_network(self, frame_bgr, entry):
        """main.py's run_inference_on_network, with the chip call emulated."""
        ns = self.ns
        ih = iw = 640
        fh, fw = frame_bgr.shape[:2]
        r = min(iw / float(fw), ih / float(fh))
        nw, nh = max(1, int(round(fw * r))), max(1, int(round(fh * r)))
        dx, dy = (iw - nw) // 2, (ih - nh) // 2
        img = np.full((ih, iw, 3), 114, dtype=np.uint8)
        img[dy:dy + nh, dx:dx + nw] = cv2.resize(frame_bgr, (nw, nh))
        order = entry.get("color_order", "natural" if ns["MODEL_COLOR_ORDER"] == "natural" else "swapped")
        img_rgb = img if order == "swapped" else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        outputs = self._predict[entry["label"]](self._tf.constant(img_rgb[None].astype(np.float32)))
        results = {f"out_{i}": t.numpy() for i, t in enumerate(outputs)}
        dets = ns["postprocess_yolov8"](results, iw, ih, entry["class_names"])
        ns["_infer_debug"].setdefault("class_scores", {})[entry["label"]] = dict(ns["_infer_debug"]["class_max"])
        return [ns["_unletterbox_det"](d, dx, dy, nw, nh, iw, ih) for d in dets]

    def raw_detections(self, frame_bgr):
        """What detect_frame hands the verifier on the Pi (frame pixels)."""
        with self._lock:
            return self.ns["detect_frame"](frame_bgr)

    def explain(self, frame_bgr, ignore_confidence=False):
        """[(det, reason or None)]. ignore_confidence: leave the minimum
        confidence to the caller (sweeps, the web tester's sliders)."""
        raw = self.raw_detections(frame_bgr)
        meta = SimpleNamespace(station=None, position=None)
        if not ignore_confidence:
            return self.verifier.explain(raw, frame_bgr, meta)
        saved = dict(config.PER_CLASS_THRESHOLDS)
        with self._lock:
            try:
                config.PER_CLASS_THRESHOLDS.update({k: 0.0 for k in saved})
                return self.verifier.explain(raw, frame_bgr, meta)
            finally:
                config.PER_CLASS_THRESHOLDS.clear()
                config.PER_CLASS_THRESHOLDS.update(saved)


def load_photo(path_or_bytes, name="", colour="auto"):
    """-> (natural-colour 1280x960 BGR frame, was the file red/blue swapped?)
    colour: "auto" (old auto_default_* photos, or leaf hue), "off", "swap"."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        image = cv2.imdecode(np.frombuffer(path_or_bytes, np.uint8), cv2.IMREAD_COLOR)
    else:
        image = cv2.imread(str(path_or_bytes))
        name = name or Path(path_or_bytes).name
    if image is None:
        raise ValueError("not an image")
    swapped = colour == "swap" or (colour == "auto" and bool(is_swapped(image, name)))
    if swapped:
        image = np.ascontiguousarray(image[:, :, ::-1])
    return to_camera_frame(image), swapped


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--har", type=Path, default=DEFAULT_HAR)
    parser.add_argument("--seg-har", type=Path, help="also emulate the fungus model (e.g. best_seg.har)")
    parser.add_argument("--evaluate", type=Path, help="labelled Roboflow export (has data.yaml)")
    parser.add_argument("--split", default="test", choices=["train", "valid", "test", "all"])
    parser.add_argument("--machine-scale", action="store_true")
    parser.add_argument("--images", type=Path, help="folder of photos: print what is found")
    parser.add_argument("--wf-cls", type=Path, help="whitefly classifier .onnx (the Pi's second stage)")
    parser.add_argument("--originals", type=Path,
                        default=HERE.parent / "whitefly datasets and negatives" / "FarmCam captures",
                        help="full-size machine photos, for the classifier's zoom crops")
    args = parser.parse_args()
    emu = EmulatedPi(args.har, args.seg_har)
    if args.wf_cls:
        # Same as main.py with the classifier installed: weaker candidates reach it.
        for key in ("whitefly", "whiteflies"):
            emu.ns["PER_CLASS_THRESHOLDS"][key] = min(emu.ns["PER_CLASS_THRESHOLDS"][key], config.WF_STAGE1_MIN)
    if args.evaluate:
        import evaluate
        import yaml
        names = yaml.safe_load((args.evaluate / "data.yaml").read_text())["names"]
        classes = ["fungus"] if "fungus" in names else ["whiteflies", "worm"]
        if classes == ["fungus"] and not args.seg_har:
            sys.exit("This is a fungus dataset: pass --seg-har best_seg.har")
        tag = "-".join(f"{p.stem}{int(p.stat().st_mtime)}" for p in (args.har, args.seg_har) if p)
        print(f"\nEvaluating {args.evaluate.name}/{args.split} on the emulated Hailo-8L ...")
        report, count = evaluate.evaluate(args.evaluate, args.split, None, "hef", None, classes,
                                          machine_scale=args.machine_scale,
                                          detector=emu.raw_detections,
                                          cache_tag=f"hef-{tag}-grid{config.WHITEFLY_TILE_GRID}"
                                                    f"-fungus{config.FUNGUS_MODEL}",
                                          wf_cls=args.wf_cls, originals=args.originals)
        evaluate.print_report(report, f"{args.evaluate.name} / {args.split} ({count} images) / "
                                      f"Hailo-8L 8-bit emulation")
    if args.images:
        for path in sorted(p for p in args.images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")):
            frame, _ = load_photo(path)
            kept = [(d[4], round(d[5], 2)) for d, r in emu.explain(frame) if r is None]
            print(f"{path.name}: {kept or 'nothing'}")


if __name__ == "__main__":
    main()

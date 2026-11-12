"""Test FarmCam detection on saved photos, on any PC (no Pi, no Hailo chip).

Runs the original .pt models instead of the .hef files, then sends their boxes
through the same backend steps as the Pi:
  1. full frame + four overlapping crops (main.py _inference_regions)
  2. raw per-class thresholds and size/shape checks (main.py _valid_box)
  3. worms from the full frame only; crops add fungus/whiteflies
  4. cross-crop merging (main.py _merge_region_detections)
  5. verifier gates 1-4: confidence, size, skin, leaf/whitefly context,
     and the worm-cancels-fungus check (verifier.py)

Not covered: the temporal check (seen in 3 of 5 frames) needs live video, and
the .hef files can differ slightly from the .pt files after quantization.

    pip install ultralytics opencv-python numpy
    python offline_test.py --images photos --det best_Pest.pt --seg best_segmentation.pt

Writes annotated images and results.csv to --out (default: offline_results).
Kept boxes are thick and coloured; rejected boxes are thin gray with the reason.
"""
import argparse
import ast
import csv
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                              # noqa: E402  (safe: constants only)
from colour_check import is_swapped        # noqa: E402
from verifier import DetectionVerifier     # noqa: E402

FRAME_W, FRAME_H = 1280, 960               # the Pi's lores stream
COLORS = {"fungus": (0, 165, 255), "whiteflies": (0, 255, 255), "worm": (0, 0, 255)}
_FUNCTIONS = {"nms", "_valid_box", "_inference_regions", "_map_crop_detection",
              "_merge_region_detections", "_norm_name", "_inference_passes"}
_CONSTANTS = {"PER_CLASS_THRESHOLDS", "PER_CLASS_MIN_BOX_AREA", "MIN_BOX_AREA",
              "MIN_ASPECT_RATIO", "MAX_ASPECT_RATIO", "NMS_IOU_THRESHOLD",
              "MAX_DETECTIONS_PER_FRAME", "CONFIDENCE_THRESHOLD", "SPLIT_COLOR_PASSES"}


def backend_definitions(tiles):
    """The Pi's own post-processing functions, taken from main.py without
    importing it (that would start the camera/web server)."""
    tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in _FUNCTIONS)
             or (isinstance(n, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id in _CONSTANTS for t in n.targets))]
    ns = dict(np=np, config=config, SMALL_OBJECT_TILES=tiles)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


def to_camera_frame(image):
    """Match the Pi's 1280x960 stream. Other aspect ratios keep their shape."""
    h, w = image.shape[:2]
    if (w, h) == (FRAME_W, FRAME_H):
        return image
    if abs(w / h - FRAME_W / FRAME_H) < 0.02:
        return cv2.resize(image, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA)
    scale = FRAME_W / float(max(w, h))
    print(f"   note: {w}x{h} is not 4:3 like the Pi camera; scaled without stretching")
    return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def raw_detections(frame, models, ns, device, model_colors="split"):
    """What detect_frame() hands the verifier on the Pi (minus temporal).

    `frame` is natural-colour BGR, like the live camera. `models` maps the
    Pi's labels ("DET", "SEG") to loaded YOLO models; the passes (colour
    order, classes kept, full frame only) come from main.py's own
    _inference_passes, so this always follows the Pi. Ultralytics turns a BGR
    array into RGB itself, so reversing the channels first makes the model see
    the swapped (teal) colours, like the Pi does for a "swapped" pass."""
    thresholds = ns["PER_CLASS_THRESHOLDS"]
    floor = min(thresholds.values())
    height, width = frame.shape[:2]
    ns["MODEL_COLOR_ORDER"] = model_colors
    entries = [{"label": label, "class_names": list(model.names.values()), "model": model}
               for label, model in models.items()]
    found = []
    for entry in ns["_inference_passes"](entries):
        model, keep = entry["model"], entry.get("keep_classes")
        regions = (ns["_inference_regions"](width, height, entry["tile_grid"]) if entry.get("tile_grid")
                   else ns["_inference_regions"](width, height))      # same as detect_frame
        for index, (left, top, right, bottom) in enumerate(regions):
            if index and entry.get("full_frame_only"):
                break
            crop = frame[top:bottom, left:right]
            if entry["color_order"] == "swapped":
                crop = np.ascontiguousarray(crop[:, :, ::-1])
            result = model.predict(crop, imgsz=640, conf=floor, iou=ns["NMS_IOU_THRESHOLD"],
                                   max_det=ns["MAX_DETECTIONS_PER_FRAME"], device=device,
                                   verbose=False)[0]
            # Letterbox scale: the size checks run in 640 model space on the Pi.
            scale = 640.0 / max(crop.shape[:2])
            polygons = result.masks.xy if result.masks is not None else None
            for i, box in enumerate(result.boxes):
                name = ns["_norm_name"](result.names[int(box.cls[0])])
                if keep and name not in keep:
                    continue
                conf = float(box.conf[0])
                if conf < thresholds.get(name, ns["CONFIDENCE_THRESHOLD"]):
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                if not ns["_valid_box"](x1 * scale, y1 * scale, x2 * scale, y2 * scale, name):
                    continue
                if index and name not in ("fungus", "whiteflies"):
                    continue          # same as detect_frame: worms from full frame only
                poly, mask_px = None, 0
                if polygons is not None and i < len(polygons) and len(polygons[i]) >= 3:
                    pts = polygons[i] + np.array([left, top], dtype=np.float32)
                    poly = pts.astype(np.int32).tolist()
                    mask_px = int(cv2.contourArea(pts.astype(np.float32)))
                found.append([x1 + left, y1 + top, x2 + left, y2 + top, name, conf, poly, mask_px])
    return ns["_merge_region_detections"](found)


def draw(frame, explained):
    out = frame.copy()
    # Rejected first so kept boxes stay on top.
    for det, reason in sorted(explained, key=lambda item: item[1] is None):
        x1, y1, x2, y2 = (int(v) for v in det[:4])
        name, conf = det[4], det[5]
        kept = reason is None
        color = COLORS.get(name, (0, 255, 0)) if kept else (160, 160, 160)
        if kept and len(det) > 6 and det[6]:
            cv2.polylines(out, [np.array(det[6], np.int32).reshape(-1, 1, 2)], True, color, 2)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3 if kept else 1)
        label = f"{name.upper()} {conf:.0%}" if kept else f"{name} {conf:.0%} [{reason}]"
        y = y1 - 6 if y1 > 18 else y2 + 16
        cv2.putText(out, label, (x1, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, label, (x1, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--images", type=Path, required=True, help="folder of JPG/PNG photos")
    parser.add_argument("--det", type=Path, required=True, help="pest model, e.g. best_Pest.pt")
    parser.add_argument("--seg", type=Path, required=True, help="fungus model, e.g. best_segmentation.pt")
    parser.add_argument("--out", type=Path, default=HERE / "offline_results")
    parser.add_argument("--station", type=int, default=None, help="apply this station's ROI (1-4)")
    parser.add_argument("--position", default="upper", choices=["upper", "lower"])
    parser.add_argument("--no-tiles", action="store_true", help="full frame only, like FARMCAM_SMALL_OBJECT_TILES=0")
    parser.add_argument("--device", default="cpu", help="cpu, or 0 for an NVIDIA GPU")
    parser.add_argument("--photos", default="auto", choices=["auto", "swapped", "natural"],
                        help="colours of the photo files; auto = by filename, then leaf hue")
    parser.add_argument("--model-colors", default=os.environ.get("FARMCAM_MODEL_COLORS", "split"),
                        choices=["split", "swapped", "natural"],
                        help="colours the models were trained on (same as FARMCAM_MODEL_COLORS on the Pi)")
    args = parser.parse_args()

    images = sorted(p for p in args.images.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not images:
        parser.error(f"No JPG/PNG images in {args.images}")
    for path in (args.det, args.seg):
        if not path.is_file():
            parser.error(f"Model not found: {path}")

    from ultralytics import YOLO
    models = {"DET": YOLO(str(args.det)), "SEG": YOLO(str(args.seg))}
    print(f"Pest model classes  : {models['DET'].names}")
    print(f"Fungus model classes: {models['SEG'].names}")
    print(f"Model colours       : {args.model_colors}  (photos: {args.photos})")

    ns = backend_definitions(tiles=not args.no_tiles)
    args.out.mkdir(parents=True, exist_ok=True)
    verifier = DetectionVerifier(rejected_dir=args.out)     # nothing is saved there
    meta = SimpleNamespace(station=args.station, position=args.position)

    with (args.out / "results.csv").open("w", newline="", encoding="utf-8") as file:
        rows = csv.writer(file)
        rows.writerow(["image", "class", "confidence", "x1", "y1", "x2", "y2", "kept", "reason"])
        for path in images:
            image = cv2.imread(str(path))
            if image is None:
                print(f"skip {path.name}: cannot read")
                continue
            swapped = {"swapped": True, "natural": False}.get(args.photos)
            if swapped is None:
                swapped = is_swapped(image, path.name)
                if swapped is None:
                    print(f"   note: cannot tell if {path.name} has swapped colours; "
                          "using it as is (pass --photos swapped or natural)")
            if swapped:
                image = np.ascontiguousarray(image[:, :, ::-1])   # natural, like the live camera
            frame = to_camera_frame(image)
            explained = verifier.explain(
                raw_detections(frame, models, ns, args.device, args.model_colors), frame, meta)
            kept = [det for det, reason in explained if reason is None]
            counts = {name: sum(d[4] == name for d in kept) for name in ("whiteflies", "worm", "fungus")}
            coverage = sum((d[7] or (d[2] - d[0]) * (d[3] - d[1])) for d in kept if d[4] == "fungus")
            coverage_pct = 100.0 * coverage / (frame.shape[0] * frame.shape[1])
            print(f"{path.name}: whiteflies={counts['whiteflies']} worm={counts['worm']} "
                  f"fungus={counts['fungus']} ({coverage_pct:.1f}% coverage), "
                  f"rejected={len(explained) - len(kept)}")
            for det, reason in explained:
                rows.writerow([path.name, det[4], f"{det[5]:.3f}", *(int(v) for v in det[:4]),
                               reason is None, reason or ""])
            cv2.imwrite(str(args.out / f"{path.stem}_result.jpg"), draw(frame, explained))
    print(f"\nAnnotated images and results.csv saved to {args.out.resolve()}")


if __name__ == "__main__":
    main()

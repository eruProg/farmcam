"""Save an unannotated frame, then compare fungus PT/HEF on identical inputs.

python3 check_fungus.py capture --out fungus.jpg
python3 check_fungus.py compare --image fungus.jpg --pt models/best_segmentation.pt
Stop farmcam.service before compare; capture needs the existing server running.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def first_jpeg(chunks, limit=16 * 1024 * 1024):
    data = bytearray()
    for chunk in chunks:
        data.extend(chunk)
        if len(data) > limit:
            raise RuntimeError("Stream exceeded the limit without a complete JPEG")
        start = data.find(b"\xff\xd8")
        end = data.find(b"\xff\xd9", start + 2) if start >= 0 else -1
        if end >= 0:
            return bytes(data[start:end + 2])
    raise RuntimeError("Stream ended without a complete JPEG")


def capture(args):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(args.url, timeout=5) as response:
        deadline = time.monotonic() + 15
        def chunks():
            while time.monotonic() < deadline:
                chunk = response.read1(65536)
                if not chunk:
                    break
                yield chunk
        jpeg = first_jpeg(chunks())
    with Path(args.out).open("xb") as file:
        file.write(jpeg)
    print("Saved unannotated frame:", Path(args.out).resolve())


def file_info(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def compare(args):
    import cv2
    from ultralytics import YOLO
    # Import defines helpers only. Never call startup(), init_camera(),
    # detect_loop(), init_pump(), or any actuation function.
    import main as server

    image_path, pt_path, hef_path = map(Path, (args.image, args.pt, args.hef))
    for path in (image_path, pt_path, hef_path):
        if not path.is_file():
            raise RuntimeError(f"File missing: {path}")
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise RuntimeError("Cannot decode the test image")
    if not server.HAS_HAILO:
        raise RuntimeError("This Python environment cannot import hailo_platform")
    model = YOLO(str(pt_path))
    if model.task != "segment" or list(model.names.values()) != ["fungus"]:
        raise RuntimeError(f"Use the original fungus-only segmentation checkpoint; got {model.task}, {model.names}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    report = {"image": file_info(image_path), "pt": file_info(pt_path), "hef": file_info(hef_path),
              "server_build": server.SERVER_BUILD, "diagnostic_threshold": 0.01,
              "production_fungus_threshold": server.PER_CLASS_THRESHOLDS["fungus"],
              "note": "Same RGB pixels per region. PT and HEF postprocessing differ; this is a diagnostic, not an accuracy benchmark.",
              "regions": []}
    try:
        server.acquire_instance_lock()
        if not server.hailo_open_device():
            raise RuntimeError("Hailo is busy/unavailable. Stop farmcam.service before comparing.")
        entry = server._configure_one_hef("SEG", hef_path, ["fungus"])
        if entry is None:
            raise RuntimeError("Could not configure the fungus HEF")
        if (entry["in_h"], entry["in_w"]) != (640, 640):
            raise RuntimeError("This comparison currently expects your 640x640 fungus HEF")
        # Diagnostic-only process: retain weak proposals without changing main.py.
        server.PER_CLASS_THRESHOLDS["fungus"] = 0.01
        for index, (x1, y1, x2, y2) in enumerate(server._inference_regions(frame.shape[1], frame.shape[0])):
            prepared = cv2.resize(frame[y1:y2, x1:x2], (640, 640))
            cv2.imwrite(str(out / f"{index}_input.png"), prepared)
            pt_result = model.predict(prepared, imgsz=640, rect=False, device="cpu",
                                      conf=0.01, iou=server.NMS_IOU_THRESHOLD,
                                      max_det=server.MAX_DETECTIONS_PER_FRAME, verbose=False)[0]
            pt_boxes = pt_result.boxes.data.cpu().tolist() if pt_result.boxes is not None else []
            pt_result.save(filename=str(out / f"{index}_pt.jpg"))
            hef_dets = server.run_inference_on_network(prepared, entry)
            drawn = prepared.copy()
            for det in hef_dets:
                a, b, c, d = map(int, det[:4])
                cv2.rectangle(drawn, (a, b), (c, d), (0, 165, 255), 2)
                cv2.putText(drawn, f"fungus {det[5]:.3f}", (a, max(15, b)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
            cv2.imwrite(str(out / f"{index}_hef.jpg"), drawn)
            region = {"index": index, "crop_xyxy": [x1, y1, x2, y2],
                      "pt_boxes_xyxy_score_class": pt_boxes,
                      "pt_best_returned_score": max((b[4] for b in pt_boxes), default=0.0),
                      "hef_best_pre_filter_score": server._infer_debug["class_max"].get("fungus", 0.0),
                      "hef_detections": hef_dets, "hef_shapes": server._infer_debug["seg_shapes"]}
            report["regions"].append(region)
            print(f"Region {index}: PT best returned={region['pt_best_returned_score']:.4f}, "
                  f"HEF pre-filter best={region['hef_best_pre_filter_score']:.4f}")
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Comparison saved:", out.resolve())
    finally:
        server.hailo_release_device()
        if server.instance_lock_file is not None:
            server.instance_lock_file.close()
            server.instance_lock_file = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cap = commands.add_parser("capture")
    cap.add_argument("--url", default="http://127.0.0.1:8080/stream-raw")
    cap.add_argument("--out", default="fungus.jpg")
    comp = commands.add_parser("compare")
    comp.add_argument("--image", default="fungus.jpg")
    comp.add_argument("--pt", default="models/best_segmentation.pt")
    comp.add_argument("--hef", default="models/best_seg.hef")
    comp.add_argument("--out", default="fungus-comparison")
    args = parser.parse_args()
    (capture if args.command == "capture" else compare)(args)


if __name__ == "__main__":
    try:
        main()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Test not completed: {exc}")

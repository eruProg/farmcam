"""
FarmCam — ONNX → HEF for the Hailo-8L, matched to the Pi's main.py
===================================================================
Run on x86-64 LINUX (Ubuntu 22.04, or WSL2 Ubuntu on Windows) with the Hailo
Dataflow Compiler installed. It does NOT run on the Pi or on Windows itself.

    python3 compile_farmcam_hef.py --onnx best_detect.onnx --out best_detect.hef \
        --calib ../datasets_fixed/pest/train/images

    python3 compile_farmcam_hef.py --onnx best_seg.onnx --out best_seg.hef \
        --calib ../datasets_fixed/fungus/train/images

Why this replaces parse_and_quantize.py + compile_hef.py:
  * Output layers: Ultralytics exports end in one decoded tensor
    (output0, 1x6x8400). main.py's postprocess reads the raw YOLOv8 heads:
    per stride 80/40/20 a 64-channel box map (cv2.*) and a class map (cv3.*),
    plus for segmentation the 32-channel mask coefficients (cv4.*) and the
    160x160x32 prototypes. The graph is cut at exactly those layers.
  * Input scale: the Pi sends raw uint8 RGB (0-255). A normalisation layer
    (/255) is compiled ON the chip, and calibration images stay 0-255.
    (The old script calibrated with 0-1 images and no normalisation layer.)
  * Calibration images are prepared like the Pi prepares camera frames:
    natural colours, letterboxed to 640x640 with grey (114) padding.

The HEF must be compiled with a Dataflow Compiler version that matches the
Pi's HailoRT (check on the Pi: `hailortcli --version`, then Hailo's
compatibility table).

Accuracy options (test each with rpi_server/hef_emulator.py --har first):
    --precise scores   16-bit class-score heads (the scores the minimums judge)
    --precise heads    16-bit box and class heads
    --har-only         stop after quantization: write the .har for the emulator
"""
import argparse
import random
import re
import sys
from pathlib import Path

import numpy as np

IMG = 640


def head_end_nodes(onnx_path: Path):
    """The raw YOLOv8 head layers in an Ultralytics ONNX export."""
    import onnx
    graph = onnx.load(str(onnx_path)).graph
    names = [n.name for n in graph.node]
    ends = []
    for branch in ("cv2", "cv3", "cv4"):          # box, class, mask coefficients
        for i in range(3):
            name = f"/model.22/{branch}.{i}/{branch}.{i}.2/Conv"
            if name in names:
                ends.append(name)
    proto = [n for n in names if n.startswith("/model.22/proto/cv3/")]
    if proto:
        ends.append(proto[-1])                    # prototype masks (segmentation)
    task = "segment" if any("cv4" in e for e in ends) else "detect"
    expected = 10 if task == "segment" else 6
    if len(ends) != expected:
        sys.exit(f"Found {len(ends)} head layers {ends}; expected {expected} for a YOLOv8 {task} model.")
    return ends, task, graph.input[0].name


def precise_layers(runner, ends, which):
    """Hailo layer names of the head convs to keep at 16 bit."""
    wanted = [e for e in ends if which == "heads" or "/cv3." in e]   # cv3 = class scores
    layers = runner.get_hn_dict()["layers"]
    names = [name.split("/")[-1] for name, layer in layers.items()
             if any(e in layer.get("original_names", []) for e in wanted)]
    if len(names) != len(wanted):
        sys.exit(f"Could not map {wanted} to Hailo layers (found {names}).")
    return names


def letterbox(img):
    import cv2
    h, w = img.shape[:2]
    r = min(IMG / w, IMG / h)
    nw, nh = max(1, round(w * r)), max(1, round(h * r))
    out = np.full((IMG, IMG, 3), 114, np.uint8)
    dx, dy = (IMG - nw) // 2, (IMG - nh) // 2
    out[dy:dy + nh, dx:dx + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return out


def calibration_set(folder: Path, count: int):
    """count images, 0-255 float32 RGB NHWC, natural colours, letterboxed."""
    import cv2
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if "auto_default" in "".join(p.name for p in paths):
        sys.exit("Calibration folder contains auto_default_* photos (old red/blue-swapped colours). "
                 "Use datasets_fixed/... made by prepare_dataset.py.")
    if len(paths) < 32:
        sys.exit(f"Only {len(paths)} images in {folder}; use at least 64.")
    random.Random(0).shuffle(paths)
    batch = []
    for p in paths[:count]:
        img = cv2.imread(str(p))
        if img is not None:
            batch.append(cv2.cvtColor(letterbox(img), cv2.COLOR_BGR2RGB).astype(np.float32))
    print(f"Calibration: {len(batch)} images from {folder}")
    return np.stack(batch)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--onnx", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--calib", type=Path, required=True, help="folder of natural-colour training images")
    ap.add_argument("--count", type=int, default=256, help="calibration images (64-1024)")
    ap.add_argument("--arch", default="hailo8l")
    ap.add_argument("--opt-level", type=int, default=1,
                    help="0-4; higher is more accurate but needs a GPU to be practical (1 is fine on CPU)")
    ap.add_argument("--precise", choices=["none", "scores", "heads"], default="none",
                    help="keep the class-score heads (scores) or all head layers (heads) at 16 bit")
    ap.add_argument("--har-only", action="store_true", help="quantize and save the .har, skip the HEF")
    args = ap.parse_args()

    from hailo_sdk_client import ClientRunner

    ends, task, input_name = head_end_nodes(args.onnx)
    # Network name inside the HEF: best_detect_v2.hef is still network "best_detect".
    name = re.sub(r"_v\d+$", "", args.out.stem)
    print(f"Model: {args.onnx.name} ({task}), cut at:\n  " + "\n  ".join(ends))

    runner = ClientRunner(hw_arch=args.arch)
    runner.translate_onnx_model(str(args.onnx), name, start_node_names=[input_name],
                                end_node_names=ends, net_input_shapes={input_name: [1, 3, IMG, IMG]})
    script = ("normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n"
              f"model_optimization_flavor(optimization_level={args.opt_level}, compression_level=0)\n")
    if args.precise != "none":
        layers = precise_layers(runner, ends, args.precise)
        print("16-bit layers: " + ", ".join(layers))
        script += f"quantization_param([{', '.join(layers)}], precision_mode=a16_w16)\n"
    runner.load_model_script(script)
    runner.optimize(calibration_set(args.calib, args.count))
    runner.save_har(str(args.out.with_suffix(".har")))
    print(f"Wrote {args.out.with_suffix('.har')}")
    if args.har_only:
        return
    hef = runner.compile()
    args.out.write_bytes(hef)
    print(f"\nWrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    try:
        from hailo_platform import HEF
        for info in HEF(str(args.out)).get_output_vstream_infos():
            print(f"  output {info.name}: {info.shape}")
    except Exception:
        print("  (install hailo_platform to list the output layers)")
    print("Copy it to the Pi's FarmCam-Server/models/ folder, keeping the name best_detect.hef / best_seg.hef.")


if __name__ == "__main__":
    main()

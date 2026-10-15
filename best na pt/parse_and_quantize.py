"""
FarmCam — Step 2: Parse ONNX → HAR and Quantize
================================================
Uses the Hailo Dataflow Compiler (hailo_sdk_client) to:
  1. Parse the ONNX graph
  2. Quantize using your calibration images

Requirements (Linux only):
    The Hailo Dataflow Compiler must be installed.
    Download from: https://hailo.ai/developer-zone/software-downloads/
    Install with:  pip install hailo_dataflow_compiler-*.whl

Calibration images are loaded from: ../rpi_server/calib/
"""

import sys
import os
import glob
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
CALIB_DIR   = SCRIPT_DIR.parent / "rpi_server" / "calib"
IMG_SIZE    = 640   # Must match export step
CALIB_COUNT = 64    # Number of calibration images to use (more = better accuracy)

MODELS = [
    {
        "onnx":    SCRIPT_DIR / "best_detect.onnx",
        "har":     SCRIPT_DIR / "best_detect.har",
        "name":    "best_detect",
        "end_node_names": [],        # Empty = auto-detect from ONNX (works for YOLOv8)
        "desc":    "Pest Detection",
    },
    {
        "onnx":    SCRIPT_DIR / "best_seg.onnx",
        "har":     SCRIPT_DIR / "best_seg.har",
        "name":    "best_seg",
        "end_node_names": [],
        "desc":    "Fungus Segmentation",
    },
]

# ─── Load calibration images ─────────────────────────────────────
def load_calib_images(limit: int = CALIB_COUNT):
    """Load calibration images as numpy arrays normalised to 0-1 float32."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("❌ opencv-python not installed. Run: pip install opencv-python-headless numpy")
        sys.exit(1)

    patterns = [
        str(CALIB_DIR / "*.jpg"),
        str(CALIB_DIR / "*.jpeg"),
        str(CALIB_DIR / "*.png"),
    ]

    paths = []
    for pat in patterns:
        paths.extend(glob.glob(pat))

    if not paths:
        print(f"❌ No calibration images found in: {CALIB_DIR}")
        print("   Calibration images are REQUIRED for quantization.")
        print("   Copy at least 50-100 representative farm images to that folder.")
        sys.exit(1)

    paths = sorted(paths)[:limit]
    print(f"   📷 Loading {len(paths)} calibration images from {CALIB_DIR}")

    images = []
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            continue
        img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        images.append(img)

    print(f"   ✅ Loaded {len(images)} images")
    return images


# ─── Parse + Quantize ────────────────────────────────────────────
def parse_and_quantize(cfg: dict, calib_images: list) -> bool:
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("❌ Hailo Dataflow Compiler not installed!")
        print("   Download it from: https://hailo.ai/developer-zone/software-downloads/")
        print("   Then run:         pip install hailo_dataflow_compiler-*.whl")
        sys.exit(1)

    import numpy as np

    onnx_path = cfg["onnx"]
    har_path  = cfg["har"]
    name      = cfg["name"]
    desc      = cfg["desc"]

    print(f"\n🔬 Parsing + Quantizing: {desc}")
    print(f"   ONNX  : {onnx_path.name}")
    print(f"   HAR   : {har_path.name}")

    if not onnx_path.exists():
        print(f"❌ ONNX not found: {onnx_path}")
        print("   Run export_onnx.py first!")
        return False

    runner = ClientRunner(hw_arch="hailo8l")

    # Parse ONNX → initial HAR
    print("   ▶ Parsing ONNX graph...")
    hn, params = runner.translate_onnx_model(
        str(onnx_path),
        name,
        start_node_names=None,        # None = auto (from input)
        end_node_names=None,          # None = auto (last layers)
        net_input_shapes={name: [1, 3, IMG_SIZE, IMG_SIZE]},
    )
    runner.save_har(str(har_path.with_suffix(".pre_quantize.har")))
    print("   ✅ ONNX parsed")

    # Quantize using calibration dataset
    print(f"   ▶ Quantizing with {len(calib_images)} calibration images...")
    calib_dataset = np.array(calib_images, dtype=np.float32)

    runner.optimize(calib_dataset)
    runner.save_har(str(har_path))
    print(f"   ✅ HAR saved: {har_path.name}")
    return True


def main():
    print("=" * 60)
    print("  FarmCam — Parse & Quantize (ONNX → HAR)")
    print("=" * 60)

    calib_images = load_calib_images()

    success = 0
    for cfg in MODELS:
        if parse_and_quantize(cfg, calib_images):
            success += 1

    print(f"\n{'='*60}")
    print(f"  Processed {success}/{len(MODELS)} models")
    print(f"{'='*60}\n")

    if success < len(MODELS):
        sys.exit(1)


if __name__ == "__main__":
    main()

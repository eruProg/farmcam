"""
FarmCam — Step 1: Export YOLOv8 .pt models to .onnx
=====================================================
Run this on Linux before the Hailo compiler steps.

Requirements:
    pip install ultralytics onnx onnxsim

Models:
    best_Pest.pt         → YOLOv8n detection  (whiteflies + worm)
    best_segmentation.pt → YOLOv8n-seg        (fungus segmentation)
"""

import sys
import os
from pathlib import Path

def check_ultralytics():
    try:
        from ultralytics import YOLO
        return YOLO
    except ImportError:
        print("❌ ultralytics not installed. Run: pip install ultralytics onnx onnxsim")
        sys.exit(1)

YOLO = check_ultralytics()

SCRIPT_DIR = Path(__file__).parent.resolve()

# ─── Model definitions ───────────────────────────────────────────
MODELS = [
    {
        "pt":      SCRIPT_DIR / "best_Pest.pt",
        "onnx":    SCRIPT_DIR / "best_detect.onnx",
        "task":    "detect",
        "imgsz":   640,
        "desc":    "Pest Detection (whiteflies + worm)",
    },
    {
        "pt":      SCRIPT_DIR / "best_segmentation.pt",
        "onnx":    SCRIPT_DIR / "best_seg.onnx",
        "task":    "segment",
        "imgsz":   640,
        "desc":    "Fungus Segmentation",
    },
]

def export_model(cfg: dict) -> bool:
    pt_path  = cfg["pt"]
    onnx_out = cfg["onnx"]
    desc     = cfg["desc"]

    print(f"\n📦 Exporting: {desc}")
    print(f"   Input : {pt_path.name}")
    print(f"   Output: {onnx_out.name}")

    if not pt_path.exists():
        print(f"❌ File not found: {pt_path}")
        return False

    model = YOLO(str(pt_path))
    print(f"   Classes: {model.names}")
    print(f"   Task   : {model.task}")

    # Export to ONNX with settings tuned for Hailo compatibility
    result = model.export(
        format   = "onnx",
        imgsz    = cfg["imgsz"],
        opset    = 11,        # Hailo DFC requires opset 11
        simplify = True,      # Run onnxsim to simplify graph
        dynamic  = False,     # Static batch size for Hailo
        half     = False,     # Full FP32 export (quantization happens in Hailo DFC)
    )

    # ultralytics saves it next to the .pt — move it to our target path
    exported = Path(str(result))
    if exported.exists() and exported != onnx_out:
        exported.rename(onnx_out)

    if onnx_out.exists():
        size_mb = onnx_out.stat().st_size / (1024 * 1024)
        print(f"   ✅ Exported successfully: {onnx_out.name}  ({size_mb:.1f} MB)")
        return True
    else:
        print(f"   ❌ Export failed — output file not found")
        return False


def main():
    print("=" * 60)
    print("  FarmCam — ONNX Export")
    print("=" * 60)

    success = 0
    for cfg in MODELS:
        if export_model(cfg):
            success += 1

    print(f"\n{'='*60}")
    print(f"  Exported {success}/{len(MODELS)} models")
    print(f"{'='*60}\n")

    if success < len(MODELS):
        sys.exit(1)


if __name__ == "__main__":
    main()

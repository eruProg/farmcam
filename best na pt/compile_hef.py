"""
FarmCam — Step 3: Compile HAR → HEF (Hailo Executable Format)
==============================================================
This is the final compilation step. It takes the quantized HAR
archive and compiles it into a HEF file that runs directly on
the Hailo-8L NPU chip on your Raspberry Pi 5.

The resulting .hef files are what your main.py uses.

Requirements:
    - Hailo Dataflow Compiler installed (same as step 2)
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

MODELS = [
    {
        "har":  SCRIPT_DIR / "best_detect.har",
        "hef":  SCRIPT_DIR / "best_detect.hef",
        "desc": "Pest Detection (whiteflies + worm)",
    },
    {
        "har":  SCRIPT_DIR / "best_seg.har",
        "hef":  SCRIPT_DIR / "best_seg.hef",
        "desc": "Fungus Segmentation",
    },
]


def compile_hef(cfg: dict) -> bool:
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("❌ Hailo Dataflow Compiler not installed!")
        print("   Download: https://hailo.ai/developer-zone/software-downloads/")
        sys.exit(1)

    har_path = cfg["har"]
    hef_path = cfg["hef"]
    desc     = cfg["desc"]

    print(f"\n🔨 Compiling: {desc}")
    print(f"   HAR input : {har_path.name}")
    print(f"   HEF output: {hef_path.name}")

    if not har_path.exists():
        print(f"❌ HAR not found: {har_path}")
        print("   Run parse_and_quantize.py first!")
        return False

    runner = ClientRunner(hw_arch="hailo8l")
    runner.load_har(str(har_path))

    print("   ▶ Compiling (this may take several minutes)...")
    hef = runner.compile()

    with open(str(hef_path), "wb") as f:
        f.write(hef)

    size_mb = hef_path.stat().st_size / (1024 * 1024)
    print(f"   ✅ HEF compiled: {hef_path.name}  ({size_mb:.1f} MB)")
    return True


def main():
    print("=" * 60)
    print("  FarmCam — Compile (HAR → HEF)")
    print("=" * 60)

    success = 0
    for cfg in MODELS:
        if compile_hef(cfg):
            success += 1

    print(f"\n{'='*60}")
    print(f"  Compiled {success}/{len(MODELS)} HEF files")
    print(f"{'='*60}\n")

    if success < len(MODELS):
        sys.exit(1)

    print("🎉 Both HEF files are ready!")
    print("")
    print("   Next: copy them to your RPi5:")
    print("   ─────────────────────────────────────────────────────")
    print("   mkdir -p ~/models")
    print("   cp best_detect.hef  ~/models/best_detect.hef")
    print("   cp best_seg.hef     ~/models/best_seg.hef")
    print("")
    print("   Or via SCP from Windows (PowerShell):")
    print("   scp best_detect.hef best_seg.hef pi@<RPi5-IP>:~/models/")
    print("   ─────────────────────────────────────────────────────")
    print("")


if __name__ == "__main__":
    main()

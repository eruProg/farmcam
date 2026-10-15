#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  FarmCam — PT → HEF Conversion Script
#  Converts best_Pest.pt (detection) and best_segmentation.pt (segmentation)
#  into best_detect.hef and best_seg.hef for Hailo-8L on RPi5
#
#  Run this on LINUX (RPi5, Ubuntu VM, or any Debian/Ubuntu system)
#  with the Hailo Dataflow Compiler installed.
#
#  Usage:
#     chmod +x convert_to_hef.sh
#     ./convert_to_hef.sh
# ═══════════════════════════════════════════════════════════════════════════

set -e  # Stop on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DETECT_PT="best_Pest.pt"
SEG_PT="best_segmentation.pt"
CALIB_DIR="../rpi_server/calib"
OUT_DETECT="best_detect.hef"
OUT_SEG="best_seg.hef"

# Hailo target chip: hailo8l for RPi5 AI Kit
HAILO_CHIP="hailo8l"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   FarmCam  →  Hailo HEF Converter                       ║"
echo "║   Target chip : Hailo-8L (RPi5 AI Kit)                  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ─── Dependency check ────────────────────────────────────────────
check_dep() {
    if ! command -v "$1" &>/dev/null; then
        echo "❌ Missing: $1"
        echo "   Install the Hailo Dataflow Compiler first."
        echo "   See: HAILO_CONVERSION_GUIDE.md"
        exit 1
    fi
}

echo "🔍 Checking dependencies..."
check_dep python3
check_dep hailomz
check_dep hailo
echo "✅ Dependencies found"
echo ""

# ─── Check PT files ──────────────────────────────────────────────
if [ ! -f "$DETECT_PT" ]; then
    echo "❌ Detection model not found: $DETECT_PT"
    exit 1
fi
if [ ! -f "$SEG_PT" ]; then
    echo "❌ Segmentation model not found: $SEG_PT"
    exit 1
fi
echo "✅ PT models found:"
echo "   Detection  : $DETECT_PT  ($(du -h "$DETECT_PT" | cut -f1))"
echo "   Segmentation: $SEG_PT  ($(du -h "$SEG_PT" | cut -f1))"
echo ""

# ─── Export PT → ONNX ────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STEP 1/3  →  Exporting .pt to .onnx"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 export_onnx.py
echo ""

# ─── ONNX → HAR (Hailo Archive) ──────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STEP 2/3  →  Parsing ONNX to HAR + Quantization"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 parse_and_quantize.py
echo ""

# ─── HAR → HEF ───────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  STEP 3/3  →  Compiling HAR to HEF"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python3 compile_hef.py
echo ""

# ─── Validate output ─────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  RESULT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for hef in "$OUT_DETECT" "$OUT_SEG"; do
    if [ -f "$hef" ]; then
        echo "  ✅ $(du -h "$hef")"
    else
        echo "  ❌ MISSING: $hef"
    fi
done

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  NEXT STEP — Copy the HEFs to your RPi5:                ║"
echo "║                                                          ║"
echo "║  mkdir -p ~/models                                       ║"
echo "║  cp best_detect.hef best_seg.hef ~/models/               ║"
echo "║                                                          ║"
echo "║  Or via SCP from your PC:                               ║"
echo "║  scp best_detect.hef best_seg.hef pi@<RPi-IP>:~/models/ ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

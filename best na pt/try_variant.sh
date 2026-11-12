#!/usr/bin/env bash
# Quantize one variant of the pest model (no HEF) and score it on the emulated
# Hailo-8L. Run in WSL Ubuntu:
#   bash "/mnt/c/Users/eru/Documents/Thesis/best na pt/try_variant.sh" v2 --precise scores
# Results: rpi_server/eval_results/variant_<name>.txt
set -eo pipefail
NAME="$1"; shift
THESIS="/mnt/c/Users/eru/Documents/Thesis"
WORK="$HOME/farmcam_compile/best na pt"
OUT="$THESIS/rpi_server/eval_results/variant_${NAME}.txt"
source "$HOME/hailo/bin/activate"
cd "$WORK"
cp "$THESIS/best na pt/compile_farmcam_hef.py" .
{
  echo "### variant $NAME: $*"
  date
  python3 compile_farmcam_hef.py --onnx "../datasets/pest pt/best_detect.onnx" \
      --out "best_detect_${NAME}.hef" --calib ../datasets_fixed/pest/train/images --har-only "$@" 2>&1 \
      | grep -E "16-bit|SNR|Wrote|rror|Calibration:" || true
  cd "$THESIS/rpi_server"
  for scale in "" "--machine-scale"; do
    python hef_emulator.py --evaluate ../datasets_fixed/pest --split test $scale \
        --har "$WORK/best_detect_${NAME}.har" 2>/dev/null \
        | grep -v -E "^\s+[0-9]+/148 images|note:"
  done
  date
} > "$OUT" 2>&1
echo "done: $OUT"

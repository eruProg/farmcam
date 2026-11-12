#!/usr/bin/env bash
# Compile pest (YOLOv8n detect) ONNX files to HEF in WSL Ubuntu, keep each .har
# for the emulator, copy each HEF back next to its ONNX, then score it on the
# emulated Hailo-8L (pest test photos + the machine photos with no pests).
#   bash "/mnt/c/Users/eru/Documents/Thesis/best na pt/compile_detect.sh" v3/best_detect_v3.onnx v3/best_detect_v3_last.onnx
# (ONNX paths relative to the Thesis folder.) Results: rpi_server/eval_results/hef_<name>.txt
set -eo pipefail
THESIS="/mnt/c/Users/eru/Documents/Thesis"
WORK="$HOME/farmcam_compile/best na pt"
CALIB="$HOME/farmcam_compile/datasets_fixed/pest/train"
mkdir -p "$WORK" "$CALIB"
source "$HOME/hailo/bin/activate"
rm -rf "$CALIB/images"
cp -r "$THESIS/datasets_fixed/pest/train/images" "$CALIB/"
cp "$THESIS/best na pt/compile_farmcam_hef.py" "$WORK/"
for rel in "$@"; do
  ONNX="$THESIS/$rel"
  NAME="$(basename "$ONNX" .onnx)"
  OUT="$THESIS/rpi_server/eval_results/hef_${NAME}.txt"
  cd "$WORK"
  python3 compile_farmcam_hef.py --onnx "$ONNX" --out "$NAME.hef" --calib "$CALIB/images"
  cp "$NAME.hef" "$(dirname "$ONNX")/$NAME.hef"
  cd "$THESIS/rpi_server"
  {
    echo "### $NAME (emulated Hailo-8L)"
    python hef_emulator.py --evaluate ../datasets_fixed/pest --split test --machine-scale \
        --har "$WORK/$NAME.har" 2>/dev/null | grep -v -E "images$|note:"
    echo "### $NAME on machine photos with no pests"
    python hef_emulator.py --evaluate ../datasets_fixed/pest_machine_test --split test \
        --har "$WORK/$NAME.har" 2>/dev/null | grep -E "labelled objects|precision |per photo"
  } > "$OUT" 2>&1
  echo "Done: $(dirname "$ONNX")/$NAME.hef, results in $OUT"
done

#!/usr/bin/env bash
# Compile a fungus (YOLOv8n-seg) ONNX to HEF in WSL Ubuntu, keep the .har for
# the emulator, and copy the HEF back to Windows next to the ONNX.
#   bash "/mnt/c/Users/eru/Documents/Thesis/best na pt/compile_seg.sh" "best na pt/seg/best_seg.onnx" best_seg
# (ONNX path relative to the Thesis folder; second argument = output name.)
set -eo pipefail
THESIS="/mnt/c/Users/eru/Documents/Thesis"
ONNX="$THESIS/$1"
NAME="${2:-best_seg}"
WORK="$HOME/farmcam_compile/best na pt"
mkdir -p "$WORK" "$HOME/farmcam_compile/datasets_fixed/fungus/train"
source "$HOME/hailo/bin/activate"
rm -rf "$HOME/farmcam_compile/datasets_fixed/fungus/train/images"
cp -r "$THESIS/datasets_fixed/fungus/train/images" "$HOME/farmcam_compile/datasets_fixed/fungus/train/"
cp "$THESIS/best na pt/compile_farmcam_hef.py" "$WORK/"
cd "$WORK"
python3 compile_farmcam_hef.py --onnx "$ONNX" --out "$NAME.hef" \
    --calib ../datasets_fixed/fungus/train/images "${@:3}"
cp "$NAME.hef" "$(dirname "$ONNX")/$NAME.hef"
echo "Done: $(dirname "$ONNX")/$NAME.hef"

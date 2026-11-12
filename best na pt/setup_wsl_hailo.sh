#!/usr/bin/env bash
# FarmCam - one-time setup of the Hailo Dataflow Compiler in WSL2 Ubuntu 22.04,
# then compile the pest model. Run inside Ubuntu:
#
#   bash "/mnt/c/Users/eru/Documents/Thesis/best na pt/setup_wsl_hailo.sh"
#
# Needs the Dataflow Compiler .whl (from the Hailo Developer Zone, version
# matching the Pi's `hailortcli --version`) in C:\Users\eru\Downloads.
set -euo pipefail

THESIS="/mnt/c/Users/eru/Documents/Thesis"
DOWNLOADS="/mnt/c/Users/eru/Downloads"
WORK="$HOME/farmcam_compile"
VENV="$HOME/hailo"

WHL=$(ls "$THESIS/best na pt"/hailo_dataflow_compiler-*.whl "$DOWNLOADS"/hailo_dataflow_compiler-*.whl \
      2>/dev/null | sort | tail -n 1 || true)
if [ -z "$WHL" ]; then
    echo "No hailo_dataflow_compiler-*.whl in 'best na pt' or $DOWNLOADS."
    echo "Download it from hailo.ai > Developer Zone > Software Downloads (Dataflow Compiler, Linux, Python 3.10)."
    exit 1
fi
echo "Compiler: $(basename "$WHL")"

echo "== System packages (asks for your Ubuntu password) =="
sudo apt-get update
sudo apt-get install -y python3.10-venv python3-dev python3-tk graphviz libgraphviz-dev build-essential

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip wheel
pip install "$WHL"
# The compiler pins numpy 1.26.4; newer OpenCV pulls numpy 2 and breaks it.
pip install onnx "numpy==1.26.4" "opencv-python-headless==4.10.0.84"

echo "== Copying files to $WORK (Linux disk is much faster than /mnt/c) =="
mkdir -p "$WORK/best na pt" "$WORK/datasets/pest pt" "$WORK/datasets_fixed/pest/train"
cp "$THESIS/best na pt/compile_farmcam_hef.py" "$WORK/best na pt/"
cp "$THESIS/datasets/pest pt/best_detect.onnx" "$WORK/datasets/pest pt/"
rm -rf "$WORK/datasets_fixed/pest/train/images"
cp -r "$THESIS/datasets_fixed/pest/train/images" "$WORK/datasets_fixed/pest/train/"
echo "   $(ls "$WORK/datasets_fixed/pest/train/images" | wc -l) calibration candidates"

echo "== Compiling (30-90+ minutes on a laptop CPU) =="
cd "$WORK/best na pt"
python3 compile_farmcam_hef.py --onnx "../datasets/pest pt/best_detect.onnx" \
    --out best_detect.hef --calib ../datasets_fixed/pest/train/images

cp best_detect.hef "$THESIS/datasets/pest pt/best_detect.hef"
echo
echo "Done: C:\\Users\\eru\\Documents\\Thesis\\datasets\\pest pt\\best_detect.hef"

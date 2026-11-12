"""Train the whitefly classifier (wf_classifier.py) on this PC's processor,
with the same settings as the Colab notebook's wf_cls cell. The network is
small (YOLOv8n-cls, 96x96 crops), so no GPU is needed; about 30-60 minutes.

    py train_wf_cls_local.py
    -> ..\\models_new\\whitefly_cls.onnx and whitefly_cls.pt
"""
import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO

THESIS = Path(__file__).resolve().parent.parent
RUNS = THESIS / "runs_local"
OUT = THESIS / "models_new"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=THESIS / "datasets_fixed" / "wf_cls")
    parser.add_argument("--epochs", type=int, default=80)
    args = parser.parse_args()
    name = args.data.name                     # e.g. wf_cls, wf_cls_machine
    YOLO("yolov8n-cls.pt").train(
        data=str(args.data), imgsz=96, epochs=args.epochs, patience=0, batch=64, workers=0, device="cpu",
        fliplr=0.5, flipud=0.5, hsv_h=0.01, hsv_s=0.4, hsv_v=0.4, erasing=0.2,
        project=str(RUNS), name=name, exist_ok=True, plots=True)
    best = RUNS / name / "weights" / "best.pt"
    metrics = YOLO(str(best)).val(data=str(args.data), split="test", imgsz=96, plots=False, device="cpu")
    print("whitefly classifier test accuracy =", round(metrics.top1, 3))
    onnx = Path(YOLO(str(best)).export(format="onnx", imgsz=96, opset=12, simplify=True, dynamic=False))
    OUT.mkdir(exist_ok=True)
    target = "whitefly_cls" if name == "wf_cls" else f"whitefly_cls_{name.split('wf_cls_')[-1]}"
    shutil.copy(onnx, OUT / f"{target}.onnx")
    shutil.copy(best, OUT / f"{target}.pt")
    print(f"Saved {OUT / (target + '.onnx')}")


if __name__ == "__main__":
    main()

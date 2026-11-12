"""Retrain both FarmCam models on this PC (CPU) with the Colab notebook's settings.

Slower than Colab's GPU (hours instead of minutes) but needs no account.
Starts from the current models, trains on ..\\datasets_fixed (natural colours,
from prepare_dataset.py), and stops early when validation stops improving.
Safe to stop: run again with --resume to continue where it left off.

    py train_models.py                 # pest, then fungus
    py train_models.py --only fungus
    py train_models.py --resume

Results: ..\\runs\\pest\\weights\\best.pt and ..\\runs\\fungus\\weights\\best.pt
"""
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "datasets_fixed"
RUNS = HERE.parent / "runs"
PT_DIR = Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt")

MODELS = {
    "pest": (PT_DIR / "best_Pest.pt", DATA / "pest" / "data.yaml"),
    "fungus": (PT_DIR / "best_segmentation.pt", DATA / "fungus" / "data.yaml"),
}
# Same as train_farmcam_colab.ipynb, except batch/workers sized for a 4-core laptop.
SETTINGS = dict(imgsz=640, epochs=40, patience=10, batch=8, workers=2, device="cpu",
                hsv_h=0.01, hsv_s=0.5, hsv_v=0.4, fliplr=0.5, flipud=0.5,
                degrees=10, scale=0.4, mosaic=1.0, close_mosaic=8,
                project=str(RUNS), exist_ok=True, plots=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=list(MODELS))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--epochs", type=int, default=SETTINGS["epochs"])
    args = parser.parse_args()

    from ultralytics import YOLO
    for name, (start, data) in MODELS.items():
        if args.only and name != args.only:
            continue
        last = RUNS / name / "weights" / "last.pt"
        if args.resume and last.exists():
            print(f"Resuming {name} from {last}")
            YOLO(str(last)).train(resume=True)
        else:
            print(f"Training {name} from {start.name} on {data}")
            YOLO(str(start)).train(data=str(data), name=name, **dict(SETTINGS, epochs=args.epochs))
        print(f"{name} done: {RUNS / name / 'weights' / 'best.pt'}")


if __name__ == "__main__":
    main()

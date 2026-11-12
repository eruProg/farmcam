"""Try verifier settings against labelled data and report precision/recall.

Uses evaluate.py's cached model outputs, so each setting takes seconds after
the first run. Change config.py only with values this script supports.

    py tune_checks.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config                       # noqa: E402
import evaluate as ev               # noqa: E402

DATASETS = HERE.parent / "datasets"
PEST = DATASETS / "whiteflies and worms"
FUNGUS = DATASETS / "fungus"

# (label, dataset, class, {config attribute: value}). Size tuples are
# (min area, max area, min aspect, max aspect) in 1280x960 frame pixels.
EXPERIMENTS = [
    ("fungus min area 25", FUNGUS, "fungus", {"PER_CLASS_SIZE.fungus": (25, 999999, 0.05, 20.0)}),
    ("fungus min area 50", FUNGUS, "fungus", {"PER_CLASS_SIZE.fungus": (50, 999999, 0.05, 20.0)}),
    ("fungus min area 100", FUNGUS, "fungus", {"PER_CLASS_SIZE.fungus": (100, 999999, 0.05, 20.0)}),
    ("fungus min area 200", FUNGUS, "fungus", {"PER_CLASS_SIZE.fungus": (200, 999999, 0.05, 20.0)}),
    ("fungus min area 400 (current)", FUNGUS, "fungus", {}),
    ("whitefly current", PEST, "whiteflies", {}),
    ("whitefly pale off", PEST, "whiteflies", {"WF_PALE_FRAC_MIN": 0.0}),
    ("whitefly pale 0.05", PEST, "whiteflies", {"WF_PALE_FRAC_MIN": 0.05}),
    ("whitefly contrast off", PEST, "whiteflies", {"WF_CONTRAST_MIN": -999}),
    ("whitefly pale+contrast off", PEST, "whiteflies", {"WF_PALE_FRAC_MIN": 0.0, "WF_CONTRAST_MIN": -999}),
    ("whitefly green ring off", PEST, "whiteflies", {"GREEN_RATIO_MIN": 0.0}),
]


def apply(settings):
    old = {}
    for key, value in settings.items():
        if "." in key:
            attr, sub = key.split(".")
            table = getattr(config, attr)
            old[key] = table[sub]
            table[sub] = value
            if sub == "fungus":
                pass
        else:
            old[key] = getattr(config, key)
            setattr(config, key, value)
    return old


def main():
    from ultralytics import YOLO
    models = {"DET": YOLO(str(ev.Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt\best_Pest.pt"))),
              "SEG": YOLO(str(ev.Path(r"C:\Users\eru\Documents\New FarmCam\New FarmCam\best na pt\best_segmentation.pt")))}
    print(f"{'setting':32s} {'min conf':>8s} {'precision':>10s} {'recall':>8s} {'F1':>5s}   best F1 (min conf, P, R)")
    for label, data, cls, settings in EXPERIMENTS:
        old = apply(settings)
        try:
            report, _ = ev.evaluate(data, "test", models, "split", "cpu", [cls])
        finally:
            apply(old)
        r = report[cls]
        t, tp, fp, fn = r["current"]
        p, rc, f = ev.pr(tp, fp, fn)
        best = max(r["sweep"], key=lambda s: ev.pr(*s[1:])[2])
        bp, br, bf = ev.pr(*best[1:])
        print(f"{label:32s} {t:8.2f} {p:10.1%} {rc:8.1%} {f:5.2f}   {bf:.2f} ({best[0]:.2f}, {bp:.0%}, {br:.0%})")


if __name__ == "__main__":
    main()

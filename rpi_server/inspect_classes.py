import torch
try:
    ckpt = torch.load("best.pt", map_location="cpu")
    model = ckpt.get("model", None)
    if model is not None:
        names = getattr(model, "names", None)
        with open("model_classes.txt", "w") as f:
            f.write(str(names))
        print("Success:", names)
    else:
        # Try ultralytics
        from ultralytics import YOLO
        y = YOLO("best.pt")
        with open("model_classes.txt", "w") as f:
            f.write(str(y.names))
        print("Success:", y.names)
except Exception as e:
    with open("model_classes.txt", "w") as f:
        f.write(f"Error: {e}")
    print("Error:", e)

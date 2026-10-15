from ultralytics import YOLO
import glob
import os

PT_PATH = "best.pt"
CALIB_DIR = "calib"

model = YOLO(PT_PATH)
images = sorted(glob.glob(os.path.join(CALIB_DIR, "*.jpg")))

print(f"Loaded model {PT_PATH}. Found {len(images)} calibration images.")

img_path = "calib/auto_default_20260512_153253_jpg.rf.d2d7b9f24a3d7597bce89ef8e281feb1.jpg"
results = model.predict(img_path, conf=0.01, verbose=False)
r = results[0]
for b in r.boxes:
    cls_id = int(b.cls[0])
    cls_name = r.names[cls_id]
    conf = float(b.conf[0])
    x1, y1, x2, y2 = b.xyxy[0].tolist()
    w = x2 - x1
    h = y2 - y1
    area = w * h
    print(f"  {cls_name}: conf={conf:.3f} | x1={x1:.1f} y1={y1:.1f} w={w:.1f} h={h:.1f} area={area:.1f}")



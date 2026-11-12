"""Visual tester for the COMPILED pest model, on the PC (no Pi needed).

Runs in WSL2 Ubuntu with the Hailo compiler venv and opens a page at
http://localhost:8765 in the Windows browser. Drop photos on it (or pick
labelled test-set samples) and it shows what the Hailo-8L would find, using
hef_emulator.py: the chip's 8-bit model plus main.py's and verifier.py's
real checks. Easiest start: double-click test_hef.bat in the Thesis folder.

    source ~/hailo/bin/activate
    python hef_test_server.py [--port 8765] [--samples ../datasets_fixed/pest]
"""
import argparse
import base64
import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import yaml

import hef_emulator as he

HERE = Path(__file__).resolve().parent
PAGE = HERE / "hef_tester" / "index.html"
MAX_UPLOAD = 40 * 1024 * 1024
NAME_MAP = {"whitefly": "whiteflies", "whiteflies": "whiteflies", "worm": "worm", "fungus": "fungus"}

state = {"emu": None, "error": None, "samples": ([], {})}


def load_model(har, seg_har):
    try:
        state["emu"] = he.EmulatedPi(har, seg_har)
    except Exception as exc:                      # shown on the page instead of a dead server
        state["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()


def sample_index(roots):
    """Labelled test photos from each set: ([{name, set, split, counts}], {set: (root, names)})."""
    items, sets = [], {}
    for root in roots:
        if not (root / "data.yaml").exists():
            continue
        names = yaml.safe_load((root / "data.yaml").read_text())["names"]
        sets[root.name] = (root, names)
        for split in ("test", "valid"):
            for img in sorted((root / split / "images").glob("*.jpg")):
                label = root / split / "labels" / f"{img.stem}.txt"
                counts = {"whiteflies": 0, "worm": 0, "fungus": 0}
                if label.exists():
                    for line in label.read_text().splitlines():
                        parts = line.split()
                        if parts:
                            cls = NAME_MAP.get(names[int(parts[0])])
                            if cls:
                                counts[cls] += 1
                items.append(dict(name=img.name, set=root.name, split=split, **counts))
    return items, sets


def ground_truth(root, names, split, name, w, h):
    import evaluate                                 # read_labels: same parsing as the scoring
    label = root / split / "labels" / f"{Path(name).stem}.txt"
    truth = [dict(name=NAME_MAP.get(g[0], g[0]), box=[round(v, 1) for v in g[1:]])
             for g in evaluate.read_labels(label, names, w, h)]
    return truth, evaluate.label_coverage(label, names, w, h) if "fungus" in names else None


def analyse(frame, swapped, source, started, truth=None, truth_coverage=None):
    emu = state["emu"]
    explained = emu.explain(frame, ignore_confidence=True)
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return dict(
        source=source, width=frame.shape[1], height=frame.shape[0], swapped=swapped,
        seconds=round(time.time() - started, 1),
        image="data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode(),
        detections=[dict(name=d[4], conf=round(float(d[5]), 3),
                         box=[round(float(v), 1) for v in d[:4]], reason=r,
                         poly=d[6] if len(d) > 6 and d[6] else None,
                         mask_px=int(d[7]) if len(d) > 7 and d[7] else 0) for d, r in explained],
        truth=truth, truth_coverage=truth_coverage)


class Handler(BaseHTTPRequestHandler):
    server_version = "FarmCamHefTester/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _ready(self):
        if state["emu"] is None:
            self._send(503, {"error": state["error"] or "The model is still loading (about a minute)."})
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/info":
                emu = state["emu"]
                classes = ("whiteflies", "worm", "fungus")
                self._send(200, dict(
                    ready=emu is not None, error=state["error"],
                    model=str(emu.har.name) if emu else None,
                    seg_model=str(emu.seg_har.name) if emu and emu.seg_har else None,
                    pest_model=he.config.PEST_MODEL, fungus_model=he.config.FUNGUS_MODEL,
                    minimums={k: he.config.PER_CLASS_THRESHOLDS[k] for k in classes},
                    floors={k: emu.ns["PER_CLASS_THRESHOLDS"][k] for k in classes} if emu else None,
                    severity={"medium": 5.0, "high": he.config.SEV_FUNGUS_HIGH_PCT},
                    fungus_min_per_frame=he.config.FUNGUS_MIN_PER_FRAME,
                    regions=len(emu.ns["_inference_regions"](he.FRAME_W, he.FRAME_H)) if emu else None,
                    samples=len(state["samples"][0])))
            elif url.path == "/api/samples":
                self._send(200, state["samples"][0])
            elif url.path == "/api/sample":
                if not self._ready():
                    return
                items, sets = state["samples"]
                item = next((i for i in items if i["name"] == query.get("name")
                             and i["set"] == query.get("set", i["set"])), None)
                if item is None:
                    self._send(404, {"error": "unknown sample"})
                    return
                started = time.time()
                root, names = sets[item["set"]]
                path = root / item["split"] / "images" / item["name"]
                frame, _ = he.load_photo(path, colour="off")     # datasets are colour-fixed already
                h, w = frame.shape[:2]
                truth, coverage = ground_truth(root, names, item["split"], item["name"], w, h)
                self._send(200, analyse(frame, False, item["name"], started, truth, coverage))
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path != "/api/detect":
            self._send(404, {"error": "not found"})
            return
        if not self._ready():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_UPLOAD:
            self._send(413, {"error": "Photo missing or larger than 40 MB."})
            return
        try:
            started = time.time()
            name = query.get("name", "upload.jpg")
            frame, swapped = he.load_photo(self.rfile.read(length), name, query.get("colour", "auto"))
            self._send(200, analyse(frame, swapped, name, started))
        except ValueError:
            self._send(400, {"error": "That file is not a photo OpenCV can read (use JPG or PNG)."})
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--har", type=Path, default=he.DEFAULT_HAR)
    parser.add_argument("--seg-har", type=Path, default=he.DEFAULT_SEG_HAR,
                        help="fungus model (used when the file exists)")
    parser.add_argument("--samples", type=Path, nargs="+",
                        default=[HERE.parent / "datasets_fixed" / "pest", HERE.parent / "datasets_fixed" / "fungus"])
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    except OSError:
        print(f"The tester is already running: open http://localhost:{args.port} in your browser.")
        return
    state["samples"] = sample_index(args.samples)
    seg_har = args.seg_har if args.seg_har and args.seg_har.exists() else None
    threading.Thread(target=load_model, args=(args.har, seg_har), daemon=True).start()
    print(f"FarmCam HEF tester: open http://localhost:{args.port} in your browser "
          f"({len(state['samples'][0])} labelled samples). Ctrl+C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Mark machine photos as healthy / fungus / unsure, one by one, in the browser.

    py label_health.py                       (or double-click label_health.bat)
    -> http://localhost:8766   keys: H healthy, F fungus, U unsure/unusable,
                               Left arrow back, Z zoom

Answers are saved after every key press to <folder>/health.json, so you can
stop and continue later. Photos from the old capture (auto_default_*) are
shown in natural colours. "Healthy" photos later become healthy examples for
the fungus model; healthy + fungus photos become a real machine test.
"""
import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

HERE = Path(__file__).resolve().parent
DEFAULT = HERE.parent / "datasets" / "to_label" / "field_test_clean" / "images"
CHOICES = {"healthy", "fungus", "unsure"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FarmCam Healthy Check</title>
<style>
:root { --bg:#f3f5f0; --panel:#fff; --line:#d9dfd2; --fg:#1d2419; --muted:#5d6a56; --ok:#2f7d32; --bad:#b4378f; --unsure:#8b9585; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#11150f; --panel:#181e15; --line:#2d3828; --fg:#e5ecdf; --muted:#9aa893; --ok:#6cc070; --bad:#f06ad0; --unsure:#77836f; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
header { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; padding:12px max(16px, env(safe-area-inset-left)); background:var(--panel); border-bottom:1px solid var(--line); }
h1 { margin:0; font-size:17px; }
.bar { flex:1 1 200px; height:8px; background:var(--line); border-radius:4px; overflow:hidden; }
.bar i { display:block; height:100%; background:var(--ok); width:0; transition:width .2s; }
.counts { font: 13px ui-monospace, Consolas, monospace; color:var(--muted); }
main { display:flex; flex-direction:column; align-items:center; gap:12px; padding:14px 16px 28px; }
.stage { width:min(1100px, 100%); background:#0b0d0a; border-radius:10px; overflow:auto; max-height:70vh; }
.stage img { display:block; width:100%; height:auto; cursor:zoom-in; }
.stage.zoom img { width:200%; cursor:zoom-out; }
.name { font: 12px ui-monospace, Consolas, monospace; color:var(--muted); word-break:break-all; text-align:center; }
.now { font-weight:600; }
.buttons { display:flex; flex-wrap:wrap; gap:10px; justify-content:center; }
button { font:inherit; font-weight:600; border:2px solid var(--line); background:var(--panel); color:var(--fg); border-radius:10px; padding:10px 18px; cursor:pointer; min-width:150px; }
button.h { border-color:var(--ok); color:var(--ok); } button.f { border-color:var(--bad); color:var(--bad); } button.u { color:var(--muted); }
button.sel.h { background:var(--ok); color:#fff; } button.sel.f { background:var(--bad); color:#fff; } button.sel.u { background:var(--unsure); color:#fff; }
kbd { font: 12px ui-monospace, Consolas, monospace; border:1px solid var(--line); border-radius:4px; padding:0 5px; margin-left:6px; }
.help { color:var(--muted); font-size:13px; max-width:760px; text-align:center; }
</style></head><body>
<header><h1>Healthy or fungus?</h1><div class="bar"><i id="bar"></i></div><span class="counts" id="counts"></span></header>
<main>
  <div class="now" id="now"></div>
  <div class="stage" id="stage"><img id="img" alt="machine photo"></div>
  <div class="name" id="name"></div>
  <div class="buttons">
    <button class="h" data-c="healthy">Healthy<kbd>H</kbd></button>
    <button class="f" data-c="fungus">Has fungus<kbd>F</kbd></button>
    <button class="u" data-c="unsure">Unsure / unusable<kbd>U</kbd></button>
    <button data-back="1">Back<kbd>&larr;</kbd></button>
  </div>
  <p class="help">Healthy = no fungus spots anywhere on the lettuce (worms or whiteflies don't matter here).
  Has fungus = any brown/dark lesion. Unsure = can't tell, blurry, black-and-white or no lettuce.
  Click the photo or press Z to zoom. Every answer is saved immediately.</p>
</main>
<script>
let names = [], answers = {}, i = 0;
const $ = (id) => document.getElementById(id);
async function load() {
  const d = await (await fetch('/api/list')).json();
  names = d.names; answers = d.answers;
  i = names.findIndex((n) => !answers[n]); if (i < 0) i = names.length - 1;
  show();
}
function show() {
  const done = names.filter((n) => answers[n]).length;
  const c = { healthy: 0, fungus: 0, unsure: 0 }; for (const n of names) if (answers[n]) c[answers[n]]++;
  $('bar').style.width = (100 * done / Math.max(1, names.length)) + '%';
  $('counts').textContent = `${done}/${names.length} done · healthy ${c.healthy} · fungus ${c.fungus} · unsure ${c.unsure}`;
  if (!names.length) { $('now').textContent = 'No photos found.'; return; }
  $('now').textContent = done === names.length && answers[names[i]] ? 'All photos done. You can close this tab (or go Back to change answers).' : `Photo ${i + 1} of ${names.length}`;
  $('img').src = '/api/photo?name=' + encodeURIComponent(names[i]);
  $('name').textContent = names[i];
  document.querySelectorAll('button[data-c]').forEach((b) => b.classList.toggle('sel', answers[names[i]] === b.dataset.c));
}
async function answer(choice) {
  const name = names[i];
  answers[name] = choice;
  await fetch('/api/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, choice }) });
  if (i < names.length - 1) i++;
  $('stage').classList.remove('zoom'); show();
}
document.querySelectorAll('button[data-c]').forEach((b) => b.onclick = () => answer(b.dataset.c));
document.querySelector('button[data-back]').onclick = () => { if (i > 0) { i--; show(); } };
$('img').onclick = () => $('stage').classList.toggle('zoom');
document.addEventListener('keydown', (e) => {
  const k = e.key.toLowerCase();
  if (k === 'h') answer('healthy'); else if (k === 'f') answer('fungus'); else if (k === 'u') answer('unsure');
  else if (e.key === 'ArrowLeft' && i > 0) { i--; show(); } else if (e.key === 'ArrowRight' && i < names.length - 1) { i++; show(); }
  else if (k === 'z') $('stage').classList.toggle('zoom');
});
load();
</script></body></html>
"""


class Labels:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.file = self.folder.parent / "health.json"
        self.names = sorted(p.name for p in self.folder.iterdir()
                            if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        self.lock = threading.Lock()
        try:
            self.answers = json.loads(self.file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            self.answers = {}

    def set(self, name, choice):
        if name not in self.names or choice not in CHOICES:
            raise ValueError("bad answer")
        with self.lock:
            self.answers[name] = choice
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.answers, indent=1, sort_keys=True))
            tmp.replace(self.file)

    def photo(self, name):
        if name not in self.names:
            raise KeyError(name)
        image = cv2.imread(str(self.folder / name))
        if "auto_default" in name:                 # old capture: red/blue reversed
            image = image[:, :, ::-1]
        h, w = image.shape[:2]
        if w > 1600:
            image = cv2.resize(image, (1600, int(h * 1600 / w)), interpolation=cv2.INTER_AREA)
        return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def make_handler(labels):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif url.path == "/api/list":
                self._send(200, {"names": labels.names, "answers": labels.answers})
            elif url.path == "/api/photo":
                try:
                    self._send(200, labels.photo(parse_qs(url.query).get("name", [""])[0]), "image/jpeg")
                except KeyError:
                    self._send(404, {"error": "no such photo"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                labels.set(body.get("name"), body.get("choice"))
                self._send(200, {"ok": True})
            except (ValueError, TypeError):
                self._send(400, {"error": "bad answer"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", type=Path, default=DEFAULT)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    labels = Labels(args.folder)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(labels))
    url = f"http://localhost:{args.port}"
    print(f"{len(labels.names)} photos, {len(labels.answers)} already answered. Open {url}  (Ctrl+C to stop)")
    print(f"Answers are saved in {labels.file}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

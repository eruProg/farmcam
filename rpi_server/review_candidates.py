"""Review page for unlabelled whitefly candidates on the machine photos.

The photos marked Null in Roboflow still hold real whiteflies (the Oct 27
session); the detector finds them and they must not be taught as "not a
whitefly". This writes ..\\review_whiteflies.html: every unlabelled machine
candidate that make_wf_crops.py left out ("unreviewed" in crops.csv), as a
close-up plus a wider view. Click Whitefly / Not, then Save answers: the browser
downloads reviewed_whiteflies.csv. Put it in the Thesis folder and run
make_wf_crops.py again; confirmed whiteflies become training examples.

    py review_candidates.py
"""
import base64
import csv
import html
import json
from pathlib import Path

import cv2

from make_wf_crops import photo_stem
from wf_classifier import crop_window

HERE = Path(__file__).resolve().parent
THESIS = HERE.parent
CROPS_CSV = THESIS / "datasets_fixed" / "wf_cls" / "crops.csv"
ORIGINALS = THESIS / "whitefly datasets and negatives" / "FarmCam captures"
OUT = THESIS / "review_whiteflies.html"


def jpeg(image, size):
    image = cv2.resize(image, (size, size), interpolation=cv2.INTER_CUBIC)
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return "data:image/jpeg;base64," + base64.b64encode(data.tobytes()).decode()


def context_crop(box, full):
    """About 8x the box: where on the plant the candidate sits."""
    x1, y1, x2, y2 = box
    side = max(60.0, 8 * max(x2 - x1, y2 - y1))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return crop_window((cx - side / 6, cy - side / 6, cx + side / 6, cy + side / 6), full)


def main():
    rows = [r for r in csv.DictReader(CROPS_CSV.open()) if r["class"] == "unreviewed"]
    rows.sort(key=lambda r: -float(r["stage1_conf"] or 0))
    items, cache = [], {}
    for r in rows:
        stem = photo_stem(r["photo"])
        if stem not in cache:
            cache.clear()
            cache[stem] = cv2.imread(str(ORIGINALS / f"{stem}.jpg"))
        full = cache[stem]
        if full is None:
            continue
        box = tuple(float(r[k]) for k in ("x1", "y1", "x2", "y2"))
        x1, y1, x2, y2 = box
        context = context_crop(box, full)
        # Mark the spot on the wide view with a thin ring.
        cv2.circle(context, (48, 48), 15, (0, 255, 255), 1, cv2.LINE_AA)
        items.append(dict(photo=stem, x1=x1, y1=y1, x2=x2, y2=y2, score=float(r["stage1_conf"] or 0),
                          close=jpeg(crop_window(box, full), 176), wide=jpeg(context, 176)))
    OUT.write_text(PAGE.replace("__ITEMS__", json.dumps(items)).replace("__COUNT__", str(len(items))),
                   encoding="utf-8")
    print(f"{len(items)} candidates -> {OUT}")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Whitefly Review</title>
<style>
:root{--bg:#f4f5f1;--card:#fff;--ink:#20251f;--muted:#646b61;--line:#dde0d7;--yes:#1f7a4d;--no:#a6402f;--accent:#176344}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif}
header{padding:20px max(16px,4vw) 8px}h1{margin:0 0 6px;font-size:22px}header p{margin:0;color:var(--muted);max-width:70ch}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;padding:12px max(16px,4vw) 110px}
.card{background:var(--card);border:2px solid var(--line);border-radius:10px;padding:8px;display:flex;flex-direction:column;gap:8px}
.card.yes{border-color:var(--yes);background:#eef7f1}.card.no{border-color:var(--no);background:#faefec}
.pics{display:grid;grid-template-columns:1fr 1fr;gap:6px}.pics img{width:100%;aspect-ratio:1;border-radius:6px;image-rendering:auto}
.meta{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
.buttons{display:grid;grid-template-columns:1fr 1fr;gap:6px}
button{font:inherit;font-weight:600;padding:9px;border-radius:7px;border:1px solid var(--line);background:#fff;cursor:pointer}
.card.yes .y{background:var(--yes);color:#fff;border-color:var(--yes)}.card.no .n{background:var(--no);color:#fff;border-color:var(--no)}
.bar{position:fixed;left:0;right:0;bottom:0;background:#20251f;color:#fff;padding:12px max(16px,4vw);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bar strong{margin-right:auto}.bar button{background:var(--accent);color:#fff;border:none}
textarea{width:100%;height:120px;margin-top:8px;font:12px monospace}
</style></head><body>
<header><h1>Is this a whitefly?</h1>
<p>__COUNT__ spots on machine photos that the detector thinks may be whiteflies but nobody labelled. Left: close-up (what the Pi checks). Right: wider view, ringed. Click <b>Whitefly</b> or <b>Not</b>. Leave a card alone if you can't tell. Then <b>Save answers</b> and put <code>reviewed_whiteflies.csv</code> in the Thesis folder.</p></header>
<main class="grid" id="grid"></main>
<div class="bar"><strong id="count"></strong><button id="save">Save answers</button><button id="show">Show as text</button></div>
<div id="textbox" hidden style="padding:0 max(16px,4vw) 110px"><textarea id="text" readonly></textarea></div>
<script>
const items = __ITEMS__;
const key = 'wf-review-answers';
let answers = {};
try { answers = JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) {}
const id = it => `${it.photo}|${it.x1}|${it.y1}`;
function store() { try { localStorage.setItem(key, JSON.stringify(answers)); } catch (_) {} }
function count() {
  const done = Object.keys(answers).length, yes = Object.values(answers).filter(a => a === 'yes').length;
  document.getElementById('count').textContent = `${done} of ${items.length} answered · ${yes} whiteflies`;
}
for (const it of items) {
  const card = document.createElement('article'); card.className = 'card ' + (answers[id(it)] || '');
  card.innerHTML = `<div class="pics"><img alt="close-up" src="${it.close}"><img alt="wider view" src="${it.wide}"></div>
    <div class="meta"><span>${it.photo.replace('field_full_','')}</span><span>score ${it.score.toFixed(2)}</span></div>
    <div class="buttons"><button class="y">Whitefly</button><button class="n">Not</button></div>`;
  const set = a => { if (answers[id(it)] === a) delete answers[id(it)]; else answers[id(it)] = a;
    card.className = 'card ' + (answers[id(it)] || ''); store(); count(); };
  card.querySelector('.y').onclick = () => set('yes');
  card.querySelector('.n').onclick = () => set('no');
  document.getElementById('grid').append(card);
}
function csvText() {
  const lines = ['photo,x1,y1,x2,y2,answer'];
  for (const it of items) if (answers[id(it)]) lines.push([it.photo, it.x1, it.y1, it.x2, it.y2, answers[id(it)]].join(','));
  return lines.join('\\n') + '\\n';
}
document.getElementById('save').onclick = () => {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csvText()], {type: 'text/csv'}));
  link.download = 'reviewed_whiteflies.csv'; link.click();
};
document.getElementById('show').onclick = () => {
  document.getElementById('textbox').hidden = false; document.getElementById('text').value = csvText();
};
count();
</script></body></html>
"""

if __name__ == "__main__":
    main()

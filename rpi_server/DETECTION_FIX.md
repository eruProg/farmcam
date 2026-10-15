# Detection startup and small-object fixes

## Updated file exists, but `/api/detection/debug` returns Not Found

The file on disk and the process answering HTTP may differ. The service could
run another script, or an old manual process could own port 8000 while the
service fails to start. A truncated `systemctl show` line cannot identify the
full script path. Use `--no-pager` to see the full command.

Copy `repair_service.py` beside the updated `main.py` on the Pi, then run:

```bash
cd /home/aipest/Desktop/FarmCam-Server
sudo /usr/bin/python3 repair_service.py
```

This checks the source and dependencies, stops `farmcam.service`, terminates
only direct Python `main.py`/`main_fixed.py` processes from this same folder,
and checks ports 8000/8080. It writes a backed-up service override pointing
at this `main.py` with the interpreter used to run the repair, preserves other
service settings, restarts, and waits for the HTTP build to match the file.
It reports success only after that check. Unrelated listeners are reported
without being stopped; hardware startup errors are printed from the journal.
An unsuccessful repair can leave the service stopped and prints diagnostics.

The override is `/etc/systemd/system/farmcam.service.d/99-farmcam-entrypoint.conf`.
To undo it, restore the printed backup (if any), otherwise remove only that
override file; then run `sudo systemctl daemon-reload` and restart `farmcam`.
This repair must run on the Pi; editing local Windows files does not apply it.

If an earlier repair printed `reset-failed ... Unit farmcam.service not loaded`
after stopping the leftover process, it aborted before starting the service.
The override was already written. Resume with `sudo systemctl start farmcam`,
then check the API after startup. The updated repair treats reset-failed as
best-effort and still attempts start; real start errors remain fatal.

## Follow-up: fungus and whiteflies missing while worms work

Current server build: `2026-09-26-small-targets-2` (printed at startup and
returned by `/api/status`). Check this value to confirm the new file is
actually running on the Pi.

The source checkpoint metadata was inspected without loading/executing the
pickle: `best_Pest.pt` declares `0: whitefly, 1: worm`,
`best_segmentation.pt` declares `0: fungus`, and `rpi_server/best.pt` declares
`0: fungus, 1: whitefly, 2: worm`. The server's plural `whiteflies` label maps
to the same class index. This verifies the supplied checkpoints, not the
provenance of any different HEF installed on the Pi.

The server now runs the whole image plus four overlapping square crops on
the 1280x960 camera frame. These crops retain more detail for small whiteflies
and fungus than resizing the entire frame to 640x640. Crop detections are
mapped back to the image and duplicate boxes are suppressed by class before
temporal confirmation. Worm detections retain the whole-frame path.
The confidence thresholds and repeat-sighting requirements are unchanged.
This costs up to five inference passes per model per captured frame; if too
slow, set `FARMCAM_SMALL_OBJECT_TILES=0` in the server environment.

Every ten seconds during detection, the terminal reports each class's best
model score, accepted boxes before crop merging, and confirmed boxes. Inspect:

```bash
curl --max-time 5 http://localhost:8000/api/detection/debug
```

- `class_scores` below the configured confidence thresholds: model predictions
  are weak; investigate focus, scale, lighting, model preprocessing/calibration
  and training examples. Lowering thresholds alone does not establish accuracy.
- Accepted boxes but no confirmed boxes: the detections are not repeating
  consistently across frames.
- Missing SEG/COMB model or an output parsing error: investigate model loading
  and the printed output shapes.
- Fungus boxes without masks: this is a segmentation-output issue, distinct
  from missing fungus boxes. The supplied SEG HEF's readable layer strings
  contain mask coefficient heads but no `proto` reference; confirm its actual
  exported output tensors on the Pi before recompiling.

Input quantization metadata is also printed on model load for diagnosing
whether the installed HEF expects the same preprocessing as the runtime.
No trained weights or HEFs were modified. Synthetic regression tests verify
that all three classes can reach the live results, including whiteflies
visible only in crops; they are not a live accuracy measurement.

The supplied log is a failed startup, not evidence that the models cannot
recognize whiteflies: libcamera says another process owns the camera, Hailo
reports error 74 (no free device), and no HEFs loaded. A background FarmCam
service plus a second manual run is a likely explanation. Confirm the owner;
do not kill arbitrary Python processes.

Copy the updated `main.py` to `/home/aipest/Desktop/FarmCam-Server/main.py`.
The ESP32 sketch was reviewed: it handles pump/buzzer/SMS commands and does not
run vision. No firmware upload is required for these detection fixes.

## Run exactly one server

For an existing service, after copying the file:

```bash
sudo systemctl restart farmcam
sudo journalctl -u farmcam -n 80 --no-pager
```

For a foreground diagnostic run, stop the service first:

```bash
sudo systemctl stop farmcam
cd /home/aipest/Desktop/FarmCam-Server
./farmcam_env/bin/python main.py
```

Use the Python environment that already has Picamera2 and HailoRT installed.
The setup script now creates new virtual environments with system packages
visible, since Pi camera/Hailo packages are normally installed by apt. Do not
rerun the full setup just to apply this code update.

If either device is still busy, inspect the owners and device availability:

```bash
sudo systemctl status farmcam --no-pager
pgrep -af 'main.py|main_fixed.py|rpicam|libcamera|hailo'
sudo fuser -v /dev/video* /dev/media* /dev/hailo*
hailortcli scan
```

Close the identified camera preview/other inference application, or stop its
service, then start FarmCam once. The new lock prevents duplicate updated
FarmCam processes; it cannot stop older copies or other camera applications.

## Code changes

- Fail startup if the camera or all detection models are unavailable; release
  camera/Hailo resources on exit. A partial split pair is now a failure and
  triggers the existing combined-model fallback.
- Keep Picamera2 `RGB888` arrays in their actual BGR byte order until the single
  BGR-to-RGB conversion for inference. See the
  [official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
- Apply each class's confidence threshold before NMS. Previously fungus at
  0.28–0.35 was discarded despite its configured 0.28 threshold.
- Preserve the single class dimension of fungus output tensors. Previously
  `squeeze()` changed HxWx1 into HxW and the parser skipped that class head.
- Use class-aware NMS so overlapping insects and fungus do not suppress each other.
- Accept standard Hailo per-class NMS outputs, preserving singleton boxes,
  empty classes and ymin/xmin coordinate order. See
  [HailoRT's Python implementation](https://github.com/hailo-ai/hailort/blob/master/hailort/libhailort/bindings/python/platform/hailo_platform/pyhailort/pyhailort.py).
- Reduce minimum model-space box area to 16 pixels for whiteflies and 25 for
  fungus (worms retain 150). Reduce minimum segmentation area from 60 to 4
  prototype pixels. These are initial tuning values; validate against real
  positives and negatives. Confidence thresholds remain 0.40 for whiteflies,
  0.28 for fungus and 0.45 for worms.
- Require repeat sightings even at tracker startup, reset tracking between
  plants, and avoid confirming the same captured frame repeatedly.
- Report capture/inference errors through `inference_error` in `/api/status`
  and `/api/system`; expose the inference count as `inference_frames`.

## Verify on the Pi

```bash
curl --max-time 5 http://localhost:8000/api/system
```

Expect `camera_active: true` and either both `seg_loaded`/`det_loaded` true or
`comb_loaded: true`. During detection windows, `inference_frames` should grow.
`inference_error` should be empty after successful inference; a nonempty value records an
error requiring investigation. Unsupported model outputs now report an error
instead of silently looking like zero detections.

The 12MP camera stream is not what the detector uses: it takes the 1280x960
lores stream and resizes the entire image to 640x640 for these HEFs. Tiny or
out-of-focus insects can still lose detail. Use a focused close view of the
leaf and verify the actual trained class order before changing confidence
thresholds further. Code changes cannot add detail absent from the image or
classes the model was not trained to recognize. No HEF has been retrained or
recompiled here.

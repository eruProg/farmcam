# Compare fungus predictions on the same image

Copy `check_fungus.py` into `/home/aipest/Desktop/FarmCam-Server/` on the Pi.
Copy the original `best na pt/best_segmentation.pt` from the Windows project
into that same Pi folder. Do not substitute the combined `best.pt`: that is
a different model. Leave the installed `models/best_seg.hef` in place.

1. With FarmCam running, put the affected leaf in focus and capture one frame:

   ```bash
   cd /home/aipest/Desktop/FarmCam-Server
   python3 check_fungus.py capture --out fungus.jpg
   ```

   Open `fungus.jpg` in the Pi file manager and check focus and visible symptoms.
   Capture reads the existing unannotated stream; it does not open a second
   camera process. Use another output name if the file already exists.
   Existing automatic spraying behavior remains active while FarmCam runs.

2. Check the comparison environment before stopping the service:

   ```bash
   ./farmcam_env/bin/python3 -c "import ultralytics, hailo_platform, cv2, fastapi, uvicorn"
   ```

   If this fails, keep the service running and provide the exact error. Use
   an environment with both the Pi's Hailo package and Ultralytics; do not
   replace the working service environment to perform this test.

3. Run the offline comparison with Hailo freed from the service:

   ```bash
   sudo systemctl stop farmcam
   ./farmcam_env/bin/python3 check_fungus.py compare --image fungus.jpg --pt best_segmentation.pt
   sudo systemctl start farmcam
   ```

   Run the final start command even if comparison fails. The comparison opens
   Hailo only, issues no ESP32 commands, and does not start the camera,
   detection loop, pump or SMS workers. It uses the source image's full view
   and the same crop selection as main.py. Each region is resized once to
   640x640, then supplied to both models. PT/HEF postprocessing differs, so
   this is a diagnostic comparison, not a quantified accuracy benchmark.

4. Send `fungus.jpg` and `fungus-comparison/report.json`. The numbered
   `_input.png`, `_pt.jpg`, and `_hef.jpg` files show each input and result.
   Use `--out another-comparison` for another run; existing folders are not
   overwritten. The diagnostic threshold is 0.01 in the comparison process
   only; production confidence and spray settings are unchanged.

If PT recognizes the affected region strongly and HEF does not, investigate
conversion, calibration and runtime decoding. If both miss it, investigate
the camera image, training examples and whether visible symptoms match the
trained class. A low score alone does not diagnose a plant disease.

The PT input and prediction options follow the
[Ultralytics prediction documentation](https://docs.ultralytics.com/modes/predict/).

# Review status and remaining issues

This is a targeted code review plus user-supplied logs, not a whole-system
hardware acceptance test. No live Raspberry Pi, ESP32, SIM, relay, pump or
camera was controlled from this Windows workspace. The mobile app has not
received a comprehensive end-to-end review in this task.

| Finding | Evidence / impact | Status |
| --- | --- | --- |
| Duplicate FarmCam process | Repair log identified PID 1678; updated service had camera-busy failures. | Stopped by repair; later debug API shows the new build and both models. Boot persistence still needs checking. |
| Whitefly prediction | Latest debug output reports one confirmed whitefly at about 0.416. | Pipeline produces predictions; correctness and recall are not measured. |
| Weak fungus score | Latest pre-filter peak about 0.0086. | Unresolved; compare on identical symptomatic images. |
| Missing fungus mask output | Nine SEG outputs include boxes/classes/coefficients but no prototype tensor required by this parser. | Real masks unavailable with the supplied outputs; HEF export needs correction for segmentation. |
| Misleading fungus coverage | main.py divides by all image pixels and falls back to box area without masks, but descriptions call this leaf coverage. Overlapping areas can also be summed. | Not a validated infected-leaf percentage; can distort severity and automatic spray decisions. Not changed here. |
| GSM registration | Most recent modem log: SIM READY, CREG 0,2. | SIM detected but searching; queued messages are not delivery confirmation. |
| Pump confirmation wording | Firmware reports relay commands/timeouts; no flow measurement is checked. Python formats 2.5 seconds with zero decimal places, displaying 2 seconds. | Logs show commanded operation, not verified liquid delivery. |
| Retry/actuation accounting | Automatic PUMP_ON can be retried after a missing acknowledgement; visit severity is marked sprayed before the pump worker confirms success. | Possible repeated commands after uncertain acknowledgements, or skipped retry after an actual failure; needs controlled testing/fix. |
| Health display during failures | Station data starts clear; health/status labels do not consistently incorporate missing/stale frames or inference errors. | A clear label is not evidence that a plant was successfully inspected. |
| Station identification | Station IDs advance on fixed timers; no position sensor is consulted by this detection loop. | Physical alignment and movement timing need hardware validation. |
| Crop inference cost | Up to five passes per model per captured frame. | Actual Pi throughput and alert latency have not been measured. |

Completed local checks cover detection parsing/filters, temporal confirmation,
crop mapping/merging, startup failure handling, repair process selection,
and pump/SMS command acknowledgement logic using mocks. They do not establish
model accuracy, SMS delivery, pump flow, or real-world station timing.

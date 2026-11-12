@echo off
rem FarmCam HEF Bench: test the compiled Hailo-8L pest model on photos, no Pi needed.
rem Runs in WSL Ubuntu (the Hailo compiler venv) and opens http://localhost:8765
echo Starting the FarmCam HEF tester (the model takes about a minute to load)...
echo Keep this window open while testing. Close it to stop.
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8765"
wsl -d Ubuntu-22.04 -- bash -lc "source ~/hailo/bin/activate && cd '/mnt/c/Users/eru/Documents/Thesis/rpi_server' && PYTHONUNBUFFERED=1 FARMCAM_PEST_MODEL=natural FARMCAM_FUNGUS_MODEL=natural python hef_test_server.py 2>&1 | grep -v -E 'tensorflow|cuda|TF_ENABLE|oneDNN|cpu_feature_guard|rebuild TensorFlow|absl'"
pause

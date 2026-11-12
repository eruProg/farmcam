"""Build ..\\pi_update.zip: the server code the Pi runs, the two new HEFs and
install_update.sh. PC-only tools (training, evaluation, the emulator) and
anything with live data are left out.

    py make_pi_update.py
"""
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
THESIS = HERE.parent
OUT = THESIS / "pi_update.zip"
# v3: fine-tuned on machine whiteflies (config: whitefly minimum 0.50)
MODELS = {"best_detect.hef": THESIS / "v3" / "best_detect_v3.hef",
          # v4: fungus + dirt (install_update.sh sets FARMCAM_FUNGUS_MODEL=natural2)
          "best_seg.hef": THESIS / "best na pt" / "seg" / "best_seg_v4.hef"}
# The whitefly classifier (second deep-learning stage) ships only after
# evaluate.py --wf-cls showed it helps; copy it here then.
OPTIONAL_MODELS = {"whitefly_cls.onnx": THESIS / "best na pt" / "whitefly_cls.onnx"}
PC_ONLY = {"hef_emulator.py", "hef_test_server.py", "evaluate.py", "offline_test.py",
           "prepare_dataset.py", "prelabel.py", "synth_whiteflies.py", "train_models.py",
           "tune_checks.py", "tune_verifier.py", "make_pi_update.py", "label_health.py",
           "machine_photo_test.py", "machine_tiles.py", "make_wf_crops.py", "review_candidates.py",
           "train_wf_cls_local.py"}
FIRMWARE = HERE / "ESP32_FARMCAM" / "ESP32_FARMCAM.ino"
FLASH_STEPS = """ESP32 firmware v2.3 (same commands as v2.2; fixes the SMS phone number)

Why: v2.2 sent SMS to "+09916425163", which the network rejects.
v2.3 turns 09XXXXXXXXX into +639XXXXXXXXX, keeps WiFi awake for the Pi's
commands, and texts "FarmCam is on and ready." once WiFi is up.

Flash it (laptop with Arduino IDE, ESP32 connected by USB):
 1. Open ESP32_FARMCAM/ESP32_FARMCAM.ino in Arduino IDE.
 2. Check FARMER_PHONE near the top (the farmer's number, e.g. 09916425163).
 3. Tools > Board > esp32 > "ESP32 Dev Module"; Tools > Port > the ESP32's COM port.
 4. Upload. If it stays on "Connecting...", hold the BOOT button until it starts.
 5. Tools > Serial Monitor at 115200 baud: you should see "Bridge v2.3",
    "[WiFi] Connected! IP: ..." and "[SIM] SMS ready".
 6. Type  SMS:test  and press Enter: the farmer's phone should get "test",
    and the monitor shows "[SMS] SUBMITTED OK".
    If it says "Not registered on GSM network": check the SIM800L antenna,
    its 5V/2A power supply and the SIM's load/registration.
"""
FOLDERS = ("display", "assets")


def server_files():
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in (".py", ".sh") and path.name not in PC_ONLY \
                and not path.name.startswith("test_") and path.name != "install_update.sh":
            yield path, f"server/{path.name}"
        elif path.is_file() and path.name.startswith("requirements") and path.suffix == ".txt":
            yield path, f"server/{path.name}"
    for folder in FOLDERS:
        for path in sorted((HERE / folder).rglob("*")):
            if path.is_file():
                yield path, f"server/{path.relative_to(HERE).as_posix()}"


def main():
    for name, path in MODELS.items():
        if not path.exists():
            raise SystemExit(f"Missing {path}")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        count = 0
        for path, arc in server_files():
            z.write(path, f"pi_update/{arc}")
            count += 1
        models = dict(MODELS, **{name: path for name, path in OPTIONAL_MODELS.items() if path.exists()})
        for name, path in models.items():
            z.write(path, f"pi_update/models/{name}")
        # Linux line endings, or bash on the Pi fails on \r.
        script = (HERE / "install_update.sh").read_bytes().replace(b"\r\n", b"\n")
        z.writestr("pi_update/install_update.sh", script)
        z.write(FIRMWARE, "pi_update/esp32/ESP32_FARMCAM/ESP32_FARMCAM.ino")
        z.writestr("pi_update/esp32/FLASH_ESP32.txt", FLASH_STEPS)
    print(f"{OUT}: {count} server files + models {', '.join(models)} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

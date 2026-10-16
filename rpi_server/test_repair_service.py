"""Repair-script selection tests; never runs systemctl or signals processes."""
from pathlib import Path, PurePosixPath
import tempfile
import unittest
import subprocess
from unittest.mock import patch, call

from repair_service import is_same_farmcam, source_build, unit_quote, service_override, start_repaired_service


class RepairServiceTests(unittest.TestCase):
    def test_updated_source_has_build_and_real_app_route(self):
        self.assertEqual(source_build(Path(__file__).with_name("main.py")), "2026-10-16-farmcam-complete-1")

    def test_comment_with_route_does_not_pass_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "main.py"
            path.write_text('SERVER_BUILD = "old"\n# /api/detection/debug\n')
            with self.assertRaises(RuntimeError):
                source_build(path)

    def test_only_same_directory_scripts_match(self):
        folder = Path(__file__).resolve().parent
        self.assertTrue(is_same_farmcam(["/usr/bin/python3", "-u", "main.py"], folder, folder))
        self.assertTrue(is_same_farmcam(["python3", str(folder / "main_fixed.py")], folder.parent, folder))
        self.assertFalse(is_same_farmcam(["python3", "main.py"], folder.parent, folder))
        self.assertFalse(is_same_farmcam(["python3", "-c", "main.py"], folder, folder))
        self.assertFalse(is_same_farmcam(["python3", "-m", "uvicorn", "main:app"], folder, folder))
        self.assertFalse(is_same_farmcam(["bash", "main.py"], folder, folder))

    def test_unit_quoting(self):
        self.assertEqual(unit_quote('/home/pi/Farm Cam/main.py'), '"/home/pi/Farm Cam/main.py"')
        self.assertEqual(unit_quote('/path/100%'), '"/path/100%%"')
        with self.assertRaises(ValueError):
            unit_quote('/path/$HOME')

    def test_working_directory_is_not_shell_quoted(self):
        result = service_override(PurePosixPath('/home/pi/Farm Cam'), '/usr/bin/python3')
        self.assertIn('WorkingDirectory=/home/pi/Farm Cam\n', result)
        self.assertIn('ExecStart=\nExecStart="/usr/bin/python3" -u "/home/pi/Farm Cam/main.py"\n', result)

    def test_unloaded_reset_does_not_prevent_start(self):
        ok = subprocess.CompletedProcess([], 0, stdout="")
        unloaded = subprocess.CompletedProcess([], 1, stdout="Unit farmcam.service not loaded.")
        with patch("repair_service.run", side_effect=[ok, unloaded, ok]) as command:
            start_repaired_service()
        self.assertEqual(command.call_args_list, [
            call("systemctl", "daemon-reload"),
            call("systemctl", "reset-failed", "farmcam", check=False),
            call("systemctl", "start", "farmcam"),
        ])

    def test_actual_start_failure_is_not_hidden(self):
        ok = subprocess.CompletedProcess([], 0, stdout="")
        error = subprocess.CalledProcessError(1, ["systemctl", "start", "farmcam"])
        with patch("repair_service.run", side_effect=[ok, ok, error]):
            with self.assertRaises(subprocess.CalledProcessError):
                start_repaired_service()


if __name__ == "__main__":
    unittest.main()

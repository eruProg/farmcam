"""Run on the Pi beside main.py: sudo /usr/bin/python3 repair_service.py

Fix farmcam.service's entry point, stop leftover instances from this same
folder, and verify the running HTTP build. Does not install dependencies.
"""
import ast
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def run(*args, check=True):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check)


def source_build(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    build, has_route = None, False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "SERVER_BUILD" for t in node.targets):
                build = ast.literal_eval(node.value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and isinstance(decorator.func.value, ast.Name)
                        and decorator.func.value.id == "app" and decorator.func.attr == "get"
                        and decorator.args and isinstance(decorator.args[0], ast.Constant)
                        and decorator.args[0].value == "/api/detection/debug"):
                    has_route = True
    if not isinstance(build, str) or not has_route:
        raise RuntimeError("main.py is missing SERVER_BUILD or /api/detection/debug. Copy the updated main.py first.")
    return build


def is_same_farmcam(args, cwd, folder):
    """Match only a direct Python script in the requested FarmCam directory."""
    if not args or not Path(args[0]).name.startswith("python"):
        return False
    # Do not interpret -c/-m arguments as script paths or kill arbitrary workers.
    for arg in args[1:]:
        if arg in ("-c", "-m"):
            return False
        if arg.startswith("-"):
            if arg not in ("-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO"):
                return False
            continue
        path = Path(arg)
        if not path.is_absolute():
            path = cwd / path
        return path.resolve() in {folder / "main.py", folder / "main_fixed.py"}
    return False


def matching_processes(folder):
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = proc.joinpath("cmdline").read_bytes().decode().strip("\0").split("\0")
            cwd = proc.joinpath("cwd").resolve(strict=True)
            if is_same_farmcam(args, cwd, folder):
                found.append((int(proc.name), args))
        except (OSError, UnicodeError):
            continue
    return found


def port_available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def unit_quote(value):
    # systemd expands percent specifiers and ExecStart environment references.
    if any(c in str(value) for c in "\r\n\0$"):
        raise ValueError("Unsupported character in service path")
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_override(folder, python):
    if "\\" in str(folder):
        raise ValueError("Backslashes in the Linux working directory are unsupported")
    command = f"ExecStart={unit_quote(python)} -u {unit_quote(folder / 'main.py')}\n"
    # WorkingDirectory is a single path, not a shell/ExecStart argument list.
    return ("[Service]\n" + f"WorkingDirectory={str(folder).replace('%', '%%')}\n"
            + "ExecStart=\n" + command)


def diagnostics():
    print(run("systemctl", "show", "farmcam", "--no-pager", "-p", "ExecStart",
              "-p", "WorkingDirectory", "-p", "MainPID", "-p", "ActiveState", check=False).stdout)
    print(run("ss", "-ltnp", check=False).stdout)
    print(run("journalctl", "-u", "farmcam", "-n", "60", "--no-pager", check=False).stdout)


def start_repaired_service():
    run("systemctl", "daemon-reload")
    # An inactive unit may be unloaded after daemon-reload. reset-failed does
    # not load it again, but start does. Do not abort before attempting start.
    reset = run("systemctl", "reset-failed", "farmcam", check=False)
    if reset.returncode:
        print("Note: reset-failed was not applied; attempting service start. "
              + reset.stdout.strip(), flush=True)
    # Real reload/start errors must still propagate to the diagnostics handler.
    run("systemctl", "start", "farmcam")


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Run this on the Raspberry Pi using sudo /usr/bin/python3 repair_service.py")
    folder = Path(__file__).resolve().parent
    target = folder / "main.py"
    expected = source_build(target)
    python = Path(sys.executable).absolute()
    override = service_override(folder, python)
    run(str(python), "-c", "import cv2, numpy, fastapi, uvicorn, picamera2, hailo_platform")
    # Check that the named service exists before making any changes.
    state = run("systemctl", "show", "farmcam", "--property=LoadState", "--value").stdout.strip()
    if state != "loaded":
        raise RuntimeError(f"farmcam.service is not loaded ({state}); no changes made")
    print("Target:", target, "Build:", expected, flush=True)
    print(run("systemctl", "show", "farmcam", "--no-pager", "-p", "ExecStart", "-p", "WorkingDirectory").stdout)
    run("systemctl", "stop", "farmcam")

    # Old manually started copies do not honor the new instance lock.
    for pid, args in matching_processes(folder):
        print(f"Stopping leftover FarmCam PID {pid}: {args}", flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):
        if not matching_processes(folder):
            break
        time.sleep(0.5)
    if matching_processes(folder):
        raise RuntimeError("A FarmCam process did not stop or is being restarted elsewhere. No force-kill attempted.")
    busy = [p for p in (8000, 8080) if not port_available(p)]
    if busy:
        raise RuntimeError(f"Ports {busy} still belong to another process. See the listener PIDs below; no unrelated process was stopped.")

    dropin = Path("/etc/systemd/system/farmcam.service.d/99-farmcam-entrypoint.conf")
    dropin.parent.mkdir(parents=True, exist_ok=True)
    if dropin.exists():
        backup = dropin.with_name(dropin.name + f".backup-{time.time_ns()}")
        backup.write_bytes(dropin.read_bytes())
        print("Previous override saved to", backup)
    dropin.write_text(override, encoding="utf-8")
    start_repaired_service()
    print("Waiting up to 90 seconds for the updated API...", flush=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = "No response yet"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:8000/api/detection/debug", timeout=2) as response:
                result = json.load(response)
            if result.get("build") == expected:
                print("SUCCESS: the running API matches the file on disk.")
                print(json.dumps(result, indent=2))
                return
            last_error = f"Wrong running build: {result.get('build')}"
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"Updated API did not start: {last_error}. Check the hardware/startup error in the logs below.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"REPAIR NOT COMPLETE: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stdout, file=sys.stderr)
        if sys.platform == "linux":
            print("Service may be stopped; inspect these diagnostics before restarting:")
            diagnostics()
        sys.exit(1)

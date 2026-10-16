"""One supervised, unprivileged Chromium window in the current desktop session."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import urllib.request


def browser_arguments(binary, profile):
    return [binary, '--kiosk', '--no-first-run', '--noerrdialogs',
            '--disable-session-crashed-bubble', '--disable-infobars',
            '--disable-pinch', '--overscroll-history-navigation=0',
            '--autoplay-policy=no-user-gesture-required',
            '--user-data-dir=' + str(profile), 'http://127.0.0.1:8090/']


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError('Run the kiosk as your desktop user, not root.')
    # Use the logged-in user's directory even when runuser inherits root's environment.
    runtime = Path(f'/run/user/{os.getuid()}')
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = runtime / 'farmcam-kiosk.lock'
    if args.stop:
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text())
                proc = Path('/proc') / str(pid)
                command = (proc/'cmdline').read_bytes().split(b'\0')
                if proc.stat().st_uid == os.getuid() and str(Path(__file__).resolve()).encode() in command:
                    os.kill(pid, signal.SIGTERM)
            except (FileNotFoundError, ValueError, ProcessLookupError):
                pass
        return
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()
        browser = shutil.which('chromium') or shutil.which('chromium-browser')
        if not browser:
            raise RuntimeError('Chromium is not installed. Install the chromium package.')
        profile = Path.home()/'.local/share/farmcam-display/chromium'
        profile.mkdir(parents=True, exist_ok=True)
        running, child, inhibitor = True, None, None

        def stop(*_):
            nonlocal running
            running = False
            if child and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        # Browser Wake Lock handles compositor idle inhibition. This also inhibits
        # logind idle/suspend while the kiosk owns the screen, without editing OS defaults.
        if shutil.which('systemd-inhibit'):
            inhibitor = subprocess.Popen(['systemd-inhibit', '--what=idle:sleep', '--mode=block',
                '--who=FarmCam', '--why=Monitoring display', 'sleep', 'infinity'])
        if not os.environ.get('WAYLAND_DISPLAY') and shutil.which('xset'):
            for options in (['s', 'off'], ['-dpms'], ['s', 'noblank']):
                subprocess.run(['xset'] + options, check=False, capture_output=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            while running:
                try:
                    with opener.open('http://127.0.0.1:8090/api/state', timeout=2):
                        pass
                except Exception:
                    time.sleep(2)
                    continue
                child = subprocess.Popen(browser_arguments(browser, profile), start_new_session=True)
                while running and child.poll() is None:
                    time.sleep(1)
                if not running:
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                    break
                time.sleep(5)
        finally:
            if inhibitor:
                inhibitor.terminate()
                inhibitor.wait(timeout=5)
            lock.seek(0); lock.truncate()


if __name__ == '__main__':
    main()

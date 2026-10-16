"""Install or remove the HDMI kiosk on an existing Raspberry Pi OS desktop.

sudo python3 install_display.py --user YOUR_DESKTOP_USER
sudo python3 install_display.py --user YOUR_DESKTOP_USER --uninstall
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

BEGIN = '# BEGIN FARMCAM DISPLAY'
END = '# END FARMCAM DISPLAY'
SOURCE = Path(__file__).resolve().parent
MANIFEST = Path('/var/lib/farmcam-display/install.json')


def command(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def without_block(text):
    return re.sub(r'(?m)^' + re.escape(BEGIN) + r'\n.*?^' + re.escape(END) + r'\n?', '', text, flags=re.S)


def autostart_text(original, launch, session):
    text = without_block(original)
    block = f'{BEGIN}\n{launch} &\n{END}\n'
    if session == 'wayfire':
        block = f'{BEGIN}\nfarmcam_display = {launch}\n{END}\n'
        section = re.search(r'(?m)^\[autostart\]\s*\n', text)
        if section:
            return text[:section.end()] + block + text[section.end():]
        return text + ('\n' if text and not text.endswith('\n') else '') + '[autostart]\n' + block
    return text + ('\n' if text and not text.endswith('\n') else '') + block


def unit_quote(value):
    value = str(value)
    if any(c in value for c in ('\n', '\r', '\x00', '$')):
        raise ValueError('Unsupported character in installation path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def service_text(user, source, python):
    unit_quote(source)  # validate; WorkingDirectory accepts a single unquoted path
    return f'''# Managed by FarmCam display installer
[Unit]
Description=FarmCam local HDMI display
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={str(source).replace('%', '%%')}
ExecStart={unit_quote(python)} {unit_quote(source / 'display_server.py')}
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
'''


class ManagedFiles:
    """Record originals before mutation; keep backups on reruns and partial failures."""
    def __init__(self, manifest, metadata=None):
        self.path = manifest
        self.data = json.loads(manifest.read_text()) if manifest.exists() else dict(metadata or {}, files={})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def write(self, path, content, owner=None, block=False):
        key = str(path)
        current = path.read_text() if path.exists() else None
        if key not in self.data['files']:
            self.data['files'][key] = dict(original=current, installed=content, block=block)
        else:
            record = self.data['files'][key]
            if not block and current not in (record['installed'], record['original'], None):
                raise RuntimeError(f'{path} changed outside the installer; refusing to overwrite it.')
            record['installed'] = content
        self.save()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if owner:
            # Only created/configured paths are owned by the desktop user.
            os.chown(path, *owner)

    def restore(self):
        for key, record in self.data['files'].items():
            path = Path(key)
            if not path.exists():
                continue
            current = path.read_text()
            if current == record['original']:
                continue  # already restored, or installation stopped before writing
            if current == record['installed']:
                if record['original'] is None:
                    path.unlink()
                else:
                    path.write_text(record['original'])
            elif record['block']:
                path.write_text(without_block(current))
            else:
                raise RuntimeError(f'{path} has later edits; restore it manually from {self.path}.')


def detect_session(uid):
    for process, session in (('labwc', 'labwc'), ('wayfire', 'wayfire')):
        if command('pgrep', '-u', str(uid), '-x', process, check=False).returncode == 0:
            return session
    display = command('loginctl', 'show-user', str(uid), '-p', 'Display', '--value', check=False).stdout.strip()
    if display:
        kind = command('loginctl', 'show-session', display, '-p', 'Type', '--value', check=False).stdout.strip()
        if kind == 'x11':
            return 'x11'
    raise RuntimeError('Desktop session not detected. Log into the Pi desktop, or pass --session labwc, wayfire, or x11.')


def install(args, account):
    if command('systemctl', 'show', 'farmcam', '-p', 'LoadState', '--value').stdout.strip() != 'loaded':
        raise RuntimeError('Set up farmcam.service first. This installer preserves its existing hardware environment.')
    session = args.session if args.session != 'auto' else detect_session(account.pw_uid)
    if not (shutil.which('chromium') or shutil.which('chromium-browser')):
        raise RuntimeError('Install Chromium first: sudo apt install chromium python3-venv')
    manifest = ManagedFiles(MANIFEST, dict(user=account.pw_name, session=session,
        farmcam_enabled=command('systemctl', 'is-enabled', 'farmcam', check=False).stdout.strip()))
    if manifest.data['user'] != account.pw_name or manifest.data['session'] != session:
        raise RuntimeError('Uninstall the existing display setup before changing its user/session.')
    manifest.save()
    env = SOURCE/'.display_env'
    command('/usr/bin/python3', '-m', 'venv', str(env))
    command(str(env/'bin/python'), '-m', 'pip', 'install', '-r', str(SOURCE/'requirements-display.txt'))
    service = Path('/etc/systemd/system/farmcam-display.service')
    if service.exists() and str(service) not in manifest.data['files'] and 'Managed by FarmCam display installer' not in service.read_text():
        raise RuntimeError('An unrelated farmcam-display.service already exists; refusing to replace it.')
    manifest.write(service, service_text(account.pw_name, SOURCE, env/'bin/python'))
    restart = command('systemctl', 'show', 'farmcam', '-p', 'Restart', '--value').stdout.strip()
    if restart not in ('always', 'on-failure'):
        manifest.write(Path('/etc/systemd/system/farmcam.service.d/40-farmcam-display-restart.conf'),
                       '# Managed by FarmCam display installer\n[Service]\nRestart=on-failure\nRestartSec=5\n')
    home = Path(account.pw_dir)
    launch = '/usr/bin/python3 ' + shlex.quote(str(SOURCE/'kiosk_runner.py'))
    owner = (account.pw_uid, account.pw_gid)
    if session == 'x11':
        target = home/'.config/autostart/farmcam-display.desktop'
        content = '[Desktop Entry]\nType=Application\nName=FarmCam Display\nExec=/usr/bin/python3 ' + unit_quote(SOURCE/'kiosk_runner.py') + '\nTerminal=false\n'
        manifest.write(target, content, owner)
    else:
        relative = '.config/labwc/autostart' if session == 'labwc' else '.config/wayfire.ini'
        target = home/relative
        default = Path('/etc/xdg/labwc/autostart') if session == 'labwc' else Path('/etc/wayfire/defaults.ini')
        if not target.exists() and not default.exists():
            raise RuntimeError(f'No desktop startup configuration found at {target} or {default}; existing desktop defaults must be preserved.')
        original = target.read_text() if target.exists() else default.read_text()
        manifest.write(target, autostart_text(original, launch, session), owner, block=True)
    # Newly created config parents must also be writable by the desktop user.
    parent = target.parent
    while parent != home and parent.is_relative_to(home):
        if parent.stat().st_uid == 0:
            os.chown(parent, *owner)
        parent = parent.parent
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable', 'farmcam', 'farmcam-display')
    command('systemctl', 'restart', 'farmcam-display')
    started = command('systemctl', 'start', 'farmcam', check=False)
    print('Display installed. Reboot or log out/in to open the full-screen monitor.')
    if started.returncode:
        print('Detection did not start. The display will wait; check journalctl -u farmcam.')


def uninstall(account):
    if not MANIFEST.exists():
        print('Display is not installed by this installer.')
        return
    managed = ManagedFiles(MANIFEST)
    if managed.data['user'] != account.pw_name:
        raise RuntimeError('Use --user ' + managed.data['user'])
    command('runuser', '-u', account.pw_name, '--', '/usr/bin/python3', str(SOURCE/'kiosk_runner.py'), '--stop', check=False)
    command('systemctl', 'disable', '--now', 'farmcam-display', check=False)
    managed.restore()
    if managed.data['farmcam_enabled'] == 'disabled':
        command('systemctl', 'disable', 'farmcam')
    command('systemctl', 'daemon-reload')
    MANIFEST.unlink()
    print('Display startup removed. Detection is not stopped. Log out/in to restore session screen settings.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', default=os.environ.get('SUDO_USER'))
    parser.add_argument('--session', choices=('auto', 'labwc', 'wayfire', 'x11'), default='auto')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0 or not args.user or args.user == 'root':
        parser.error('Run on the Pi with sudo and --user YOUR_DESKTOP_USER (not root).')
    import pwd
    account = pwd.getpwnam(args.user)
    try:
        uninstall(account) if args.uninstall else install(args, account)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + ('\n' + exc.stderr if isinstance(exc, subprocess.CalledProcessError) else '') + '\n')


if __name__ == '__main__':
    main()

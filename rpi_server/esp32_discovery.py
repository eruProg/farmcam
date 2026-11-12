"""Keep track of the ESP32 (pump, buzzer, SMS) and find it again by itself.

The phone hotspot hands out new IP addresses, so a hard-coded ESP32_IP breaks
the pump and SMS whenever the hotspot restarts. This monitor:
  - checks http://<ip>/status every CHECK_INTERVAL_S (the ESP32 only answers
    commands otherwise, so "reachable" would read false while idle)
  - after MISSES_BEFORE_SCAN failed checks, scans the Pi's own /24 for a
    FarmCam ESP32 (its /status JSON has 'pump' plus 'sim' or 'rssi')
  - switches to the found address and saves it for the next boot

At most one network scan per SCAN_INTERVAL_S. Everything runs in one
background thread; callers read .ip / .reachable.
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

CHECK_INTERVAL_S = 15.0
MISSES_BEFORE_SCAN = 2
SCAN_INTERVAL_S = 120.0
PROBE_TIMEOUT_S = 0.6
DOWN_ALERT_S = 120.0    # report "not responding" after this long without an answer


def looks_like_farmcam_esp32(data) -> bool:
    return isinstance(data, dict) and "pump" in data and ("sim" in data or "rssi" in data)


def probe(ip: str, port: int = 80, timeout: float = PROBE_TIMEOUT_S) -> bool:
    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/status", timeout=timeout) as resp:
            return looks_like_farmcam_esp32(json.loads(resp.read().decode("utf-8", errors="ignore")))
    except Exception:
        return False


def candidate_hosts(own_ip: str):
    """Every other host on the Pi's /24, nearest addresses first."""
    try:
        net = ipaddress.ip_network(f"{own_ip}/24", strict=False)
        own = int(ipaddress.ip_address(own_ip))
    except ValueError:
        return []
    hosts = [h for h in net.hosts() if int(h) != own]
    return [str(h) for h in sorted(hosts, key=lambda h: abs(int(h) - own))]


def scan(own_ip: str, port: int = 80, probe_fn=probe, workers: int = 32) -> Optional[str]:
    hosts = candidate_hosts(own_ip)
    if not hosts:
        return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ip, ok in zip(hosts, pool.map(lambda h: probe_fn(h, port), hosts)):
            if ok:
                return ip
    return None


class Esp32Monitor:
    def __init__(self, ip: str, port: int, own_ip_fn: Callable[[], str],
                 save_path: Optional[Path] = None, on_change: Optional[Callable[[str], None]] = None,
                 probe_fn=probe, clock=time.time,
                 on_status: Optional[Callable[[bool, str], None]] = None) -> None:
        self.ip, self.port = ip, port
        self._own_ip_fn = own_ip_fn
        self._save_path = Path(save_path) if save_path else None
        self._on_change = on_change
        self._on_status = on_status     # (False, why) after DOWN_ALERT_S silent; (True, ip) when back
        self._probe = probe_fn
        self._clock = clock
        self._started = clock()
        self._down_reported = False
        self.last_ok = 0.0
        self.misses = 0
        self.last_scan = 0.0
        self.scanning = False
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def reachable(self) -> bool:
        return self._clock() - self.last_ok < CHECK_INTERVAL_S * 2.5

    def note_ok(self) -> None:
        """Called when any ESP32 command was acknowledged."""
        self.last_ok, self.misses = self._clock(), 0
        if self._down_reported:
            self._down_reported = False
            self._report(True, self.ip)

    def _report(self, up: bool, detail: str) -> None:
        if self._on_status:
            try:
                self._on_status(up, detail)
            except Exception:
                pass

    @staticmethod
    def load_saved(path: Path) -> Optional[str]:
        try:
            ip = json.loads(Path(path).read_text()).get("ip")
            ipaddress.ip_address(ip)
            return ip
        except Exception:
            return None

    def check_once(self) -> None:
        """One health check; scans the network when the ESP32 keeps missing."""
        if self._probe(self.ip, self.port):
            self.note_ok()
            return
        self.misses += 1
        self.last_error = f"no answer from {self.ip}"
        now = self._clock()
        if self.misses >= MISSES_BEFORE_SCAN and now - self.last_scan >= SCAN_INTERVAL_S:
            self.last_scan, self.scanning = now, True
            try:
                found = scan(self._own_ip_fn(), self.port, self._probe)
            finally:
                self.scanning = False
            if found and found != self.ip:
                self._switch(found)
            elif found:
                self.note_ok()
            else:
                self.last_error = f"ESP32 not found on the network (last known {self.ip})"
        silent_s = self._clock() - max(self.last_ok, self._started)
        if not self._down_reported and silent_s >= DOWN_ALERT_S:
            self._down_reported = True
            self._report(False, self.last_error)

    def _switch(self, ip: str) -> None:
        print(f"📡 ESP32 found at a new address: {self.ip} -> {ip}")
        self.ip = ip
        self.note_ok()
        self.last_error = ""
        if self._save_path:
            try:
                self._save_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_path.write_text(json.dumps({"ip": ip, "saved_at": self._clock()}))
            except OSError as exc:
                print(f"⚠ Could not save ESP32 address: {exc}")
        if self._on_change:
            self._on_change(ip)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="esp32-monitor")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception as exc:          # never let the monitor die
                self.last_error = str(exc)
            self._stop.wait(CHECK_INTERVAL_S)

    def state(self) -> dict:
        return {"ip": self.ip, "reachable": self.reachable, "scanning": self.scanning,
                "last_ok": self.last_ok or None, "last_error": "" if self.reachable else self.last_error}

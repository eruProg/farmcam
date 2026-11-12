"""Send SMS through whichever firmware the ESP32 runs.

  v3.0 (farmcam_esp32.ino): has /sms/status; SmsBroker sends SMS_JSON with the
        recipient number and follows the acknowledgement.
  v2.x (ESP32_FARMCAM.ino): only "SMS:<text>"; the farmer's number is built
        into the firmware, and /status reports the last result (sms_result).

Before this, alerts went only through SmsBroker (v3) and were skipped when
FARMCAM_FARMER_PHONE was empty, so with the v2.2 firmware no SMS was sent.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import config

PROBE_EVERY_S = 300.0


def normalize_phone(number: str | None) -> str:
    """Philippine local numbers (09XXXXXXXXX) -> +639XXXXXXXXX; others kept."""
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("+"):
        return digits
    if digits.startswith("09") and len(digits) == 11:
        return "+63" + digits[1:]
    if digits.startswith("63") and len(digits) == 12:
        return "+" + digits
    return digits


def gsm_text(text: str, limit: int = 160) -> str:
    """Printable ASCII only (SIM800L text mode), single spaces."""
    clean = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    return " ".join(clean.split())[:limit]


def _get_json(path: str, timeout: float = 2.0):
    url = f"http://{config.ESP32_IP}:{config.ESP32_PORT}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


class SmsRouter:
    def __init__(self, broker, esp32_cmd, get_json=_get_json, clock=time.monotonic, on_failed=None):
        self._broker = broker
        self._cmd = esp32_cmd
        self._get_json = get_json
        self._clock = clock
        self._on_failed = on_failed      # called once per failed SMS with the reason
        self._failure_reported = None
        self._sent_count = 0
        self._lock = threading.Lock()
        self._firmware = None            # (ip, "v3" | "v2", checked_at)
        self._v2 = {"baseline": None, "busy": False}
        self.last = {"status": "idle", "via": None, "err": "", "to": None}

    def firmware(self) -> str | None:
        """'v3', 'v2', or None when the ESP32 does not answer."""
        ip = config.ESP32_IP
        with self._lock:
            cached = self._firmware
        if cached and cached[0] == ip and self._clock() - cached[2] < PROBE_EVERY_S:
            return cached[1]
        try:
            data = self._get_json("/sms/status?id=probe")
            kind = "v3" if isinstance(data, dict) and "boot_id" in data else "v2"
        except urllib.error.HTTPError:
            kind = "v2"                  # v2.x answers 404 for /sms/status
        except Exception:
            return None
        with self._lock:
            self._firmware = (ip, kind, self._clock())
        return kind

    def send(self, text: str, to: str | None = None) -> dict:
        text = gsm_text(text)
        kind = self.firmware()
        if kind == "v3":
            phone = normalize_phone(to or config.FARMER_PHONE)
            if not phone:
                result = dict(ok=False, status="failed", via="v3",
                              err="No farmer phone set (FARMCAM_FARMER_PHONE)", to=None)
            else:
                try:
                    msg_id = self._broker.send(phone, text) if self._broker else ""
                    result = dict(ok=bool(msg_id), status="queued" if msg_id else "failed", via="v3",
                                  err="" if msg_id else "SMS queue full", to=phone, id=msg_id)
                except ValueError as exc:
                    result = dict(ok=False, status="failed", via="v3", err=str(exc), to=phone)
        else:
            # v2.x, or not reachable right now (esp32_cmd then fails fast; in
            # dev mode it answers OK). Never retried: an unanswered request may
            # already have queued the SMS, and a retry would send it twice.
            # /status keeps the PREVIOUS SMS result, so remember it first.
            try:
                baseline = str(self._get_json("/status").get("sms_result", ""))
            except Exception:
                baseline = None
            with self._lock:
                self._v2 = {"baseline": baseline, "busy": False}
            reply = self._cmd("SMS:" + text, timeout=15.0, retries=0)
            ok = bool(reply) and str(reply).startswith("OK")
            result = dict(ok=ok, status="queued" if ok else "failed", via="v2",
                          err="" if ok else "ESP32 did not accept the SMS (not reachable?)",
                          to="number set in the ESP32 firmware")
        with self._lock:
            self.last = {k: result.get(k) for k in ("status", "via", "err", "to", "id")}
            self._sent_count += 1
            self.last["n"] = self._sent_count
        print(f"📱 SMS via ESP32 {result['via']}: {result['status']}"
              + (f" ({result['err']})" if result["err"] else ""))
        if not result["ok"]:
            self._report_failure(self.last)
        return result

    def _report_failure(self, last):
        """Tell on_failed once per message (send() or a later status() poll)."""
        if not self._on_failed or self._failure_reported == last.get("n"):
            return
        self._failure_reported = last.get("n")
        try:
            self._on_failed(last.get("err") or "")
        except Exception:
            pass

    def status(self) -> dict:
        """Last SMS: queued / sending / sent / failed, for the app."""
        with self._lock:
            last = dict(self.last)
        if last.get("via") == "v3" and self._broker:
            status = dict(self._broker.last_status(), via="v3")
            if status.get("status") == "failed":
                self._report_failure(dict(last, err=status.get("err")))
            return status
        if last.get("via") == "v2" and last.get("status") in ("queued", "sending"):
            try:
                data = self._get_json("/status")
                result = str(data.get("sms_result", ""))
                with self._lock:
                    track = self._v2
                if result == "sending" or int(data.get("sms_queued") or 0) > 0:
                    track["busy"] = True
                    # Waiting for the SIM800L (network registration etc.).
                    last.update(status="sending", err=data.get("sim_error") or "")
                elif track["busy"] or result != track["baseline"]:
                    if "submitted" in result:
                        last.update(status="sent", err="")
                    elif "fail" in result or "unconfirmed" in result:
                        last.update(status="failed", err=data.get("sim_error") or result)
                with self._lock:
                    self.last.update(status=last["status"], err=last["err"])
                if last["status"] == "failed":
                    self._report_failure(last)
            except Exception:
                pass
        return last

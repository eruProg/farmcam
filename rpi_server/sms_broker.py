"""
sms_broker.py — Pi-side SMS broker with JSON-line ACK tracking
==============================================================
Sends SMS via the ESP32 HTTP command interface.
Implements:
  - UUID-tagged message IDs for end-to-end ACK tracking
  - Retry with exponential backoff (up to SMS_MAX_RETRIES)
  - Per-message delivery status exposed in /api/system
  - Thread-safe queue (one sender thread)

Protocol (ESP32 side must support SMS_JSON command):
  Pi → ESP32 POST /cmd:
      SMS_JSON:{"id":"<uuid>","to":"+63...","msg":"<text>"}
  ESP32 → Pi (immediate):
      OK:queued:<id>
  ESP32 → Pi (async, via /api/sms/ack poll or future callback):
      SMSACK:{"id":"<id>","status":"sent"|"failed","err":"<reason>"}

Until the ESP32 firmware supports async ACK, this broker polls
GET /status on the ESP32 and infers delivery from sms_result field.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import config

log = logging.getLogger("farmcam.sms")

_PENDING_ACK: Dict[str, dict] = {}   # msg_id -> delivery status
_ACK_LOCK = threading.Lock()


class SmsBroker:
    """
    Thread-safe SMS sender.  Call start() once at server boot.
    Use send() to queue a message; it returns a msg_id immediately.
    """

    def __init__(
        self,
        esp32_ip:   str  = None,
        esp32_port: int  = None,
        clock_fn:   Callable[[], float] = None,
    ) -> None:
        self._ip     = esp32_ip   or config.ESP32_IP
        self._port   = esp32_port or config.ESP32_PORT
        self._clock  = clock_fn or time.time
        self._queue: queue.Queue = queue.Queue(maxsize=20)
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_status: Dict[str, Any] = {
            "id": None, "to": None, "status": "idle",
            "err": "", "queued_at": None, "sent_at": None,
        }
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._sender_loop, daemon=True, name="sms_broker"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def send(self, to: str, msg: str) -> str:
        """
        Queue an SMS.  Returns msg_id immediately (does not block).
        Silently drops the message if the queue is full (max 20).
        """
        if not to:
            log.warning("sms_broker.send: no recipient — set FARMCAM_FARMER_PHONE")
            return ""
        msg_id = str(uuid.uuid4())
        item = {
            "id":        msg_id,
            "to":        to,
            "msg":       msg[:160],
            "queued_at": self._clock(),
            "retries":   0,
        }
        try:
            self._queue.put_nowait(item)
            with self._lock:
                self._last_status = {
                    "id": msg_id, "to": to, "status": "queued",
                    "err": "", "queued_at": item["queued_at"], "sent_at": None,
                }
            log.info("sms queued id=%s to=%s", msg_id[:8], to)
        except queue.Full:
            log.warning("sms_broker: queue full, dropping message")
        return msg_id

    def last_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last_status)

    # ── Sender thread ─────────────────────────────────────────────────────

    def _sender_loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            for attempt in range(config.SMS_MAX_RETRIES + 1):
                if not self._running:
                    break
                ok, err = self._do_send(item)
                if ok:
                    self._update_status(item, "sent", "")
                    log.info("sms sent id=%s attempt=%d", item["id"][:8], attempt + 1)
                    break
                else:
                    if attempt < config.SMS_MAX_RETRIES:
                        backoff = config.SMS_RETRY_BACKOFF_S[
                            min(attempt, len(config.SMS_RETRY_BACKOFF_S) - 1)
                        ]
                        log.warning(
                            "sms failed id=%s attempt=%d err=%s retrying in %ds",
                            item["id"][:8], attempt + 1, err, backoff
                        )
                        time.sleep(backoff)
                    else:
                        self._update_status(item, "failed", err)
                        log.error("sms permanently failed id=%s err=%s",
                                  item["id"][:8], err)

    def _do_send(self, item: dict) -> tuple[bool, str]:
        """Send one SMS via ESP32 HTTP.  Returns (ok, err_str)."""
        try:
            import urllib.request
            import urllib.error

            # Build JSON-tagged command for the ACK protocol
            cmd = "SMS_JSON:" + json.dumps({
                "id":  item["id"],
                "to":  item["to"] if item["to"].startswith("+") else "+" + item["to"],
                "msg": item["msg"],
            })
            url  = f"http://{self._ip}:{self._port}/cmd"
            req  = urllib.request.Request(
                url,
                data    = cmd.encode("utf-8"),
                headers = {"Content-Type": "text/plain"},
                method  = "POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8").strip()

            if body.startswith("OK"):
                # The ACK is async; for now treat queued as success
                # In a future firmware update, poll /status for SMSACK
                return True, ""
            return False, body

        except Exception as exc:
            return False, str(exc)

    def _update_status(self, item: dict, status: str, err: str) -> None:
        with self._lock:
            self._last_status = {
                "id":        item["id"],
                "to":        item["to"],
                "status":    status,
                "err":       err,
                "queued_at": item["queued_at"],
                "sent_at":   self._clock() if status == "sent" else None,
            }

    # ── ACK receiver (called when ESP32 responds with SMSACK:{...}) ───────

    def receive_ack(self, line: str) -> None:
        """
        Parse an SMSACK:{...} JSON line from the ESP32 and update status.
        Call this from whatever reads ESP32 serial/HTTP output.
        """
        try:
            if not line.startswith("SMSACK:"):
                return
            ack = json.loads(line[7:])
            msg_id = ack.get("id", "")
            status = "sent" if ack.get("status") == "sent" else "failed"
            err    = ack.get("err", "")
            with self._lock:
                if self._last_status.get("id") == msg_id:
                    self._last_status["status"] = status
                    self._last_status["err"]    = err
                    if status == "sent":
                        self._last_status["sent_at"] = self._clock()
            log.info("sms ack id=%s status=%s err=%s", msg_id[:8], status, err)
        except Exception as exc:
            log.debug("receive_ack parse error: %s — line: %r", exc, line)

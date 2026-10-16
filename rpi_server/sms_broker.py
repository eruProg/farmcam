"""Bounded SMS queue with correlated ESP32 HTTP acknowledgements.
Sent means +CMGS modem submission, not delivery to the recipient's handset.
"""
import json
import queue
import re
import threading
import time
import urllib.request
import urllib.error
import uuid
import config

class SmsBroker:
    def __init__(self, esp32_ip=None, esp32_port=None, clock_fn=None):
        self._base = f'http://{esp32_ip or config.ESP32_IP}:{esp32_port or config.ESP32_PORT}'
        self._clock = clock_fn or time.time
        self._queue = queue.Queue(maxsize=20)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._statuses = {}
        self._last_id = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._sender_loop, daemon=True, name='sms')
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=6)

    def send(self, to, msg):
        if not re.fullmatch(r'\+?[0-9]{10,15}', to or ''):
            raise ValueError('Use a phone number with country code')
        # The SIM800L transport uses text mode and the firmware's compact JSON
        # parser. Keep the payload printable ASCII, without UART control bytes.
        message = ''.join(c if 32 <= ord(c) <= 126 else ' ' for c in msg)[:160]
        item = dict(id=str(uuid.uuid4()), to=to, msg=message, queued_at=self._clock())
        with self._lock:
            self._statuses[item['id']] = dict(item, status='queued', err='', sent_at=None)
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                del self._statuses[item['id']]
                return ''
            self._last_id = item['id']
            if len(self._statuses) > 100:
                self._statuses.pop(next(iter(self._statuses)))
        return item['id']

    def last_status(self):
        with self._lock:
            data = dict(self._statuses.get(self._last_id, dict(id=None, status='idle', err='')))
            data.pop('msg', None)
            data.pop('to', None)
            return data

    def _update_status(self, item, status, err=''):
        with self._lock:
            self._statuses[item['id']].update(status=status, err=err,
                sent_at=self._clock() if status == 'sent' else None)

    def receive_ack(self, line):
        try:
            if not line.startswith('SMSACK:'):
                return
            ack = json.loads(line[7:])
            if ack.get('status') not in ('queued', 'sending', 'sent', 'failed', 'unknown'):
                return
            with self._lock:
                item = self._statuses.get(ack.get('id'))
                if item:
                    item.update(status=ack['status'], err=ack.get('err', ''),
                                sent_at=self._clock() if ack['status'] == 'sent' else None)
        except (ValueError, TypeError):
            return

    def _request(self, path, body=None):
        req = urllib.request.Request(self._base + path, data=body,
                                     headers={'Content-Type': 'text/plain'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode('utf-8').strip()

    def _do_send(self, item):
        payload = 'SMS_JSON:' + json.dumps({k: item[k] for k in ('id', 'to', 'msg')}, separators=(',', ':'))
        body = self._request('/cmd', payload.encode())
        return body == 'OK:queued:' + item['id'] or body == 'OK:known:' + item['id']

    def _process(self, item):
        # Confirm protocol support before submitting. Never resend after an ambiguous
        # transport failure or device reboot: the original SMS may have reached the modem.
        try:
            initial = json.loads(self._request('/sms/status?id=' + item['id']))
            boot = initial['boot_id']
            if initial.get('id') != item['id']:
                raise ValueError('Uncorrelated SMS status')
        except Exception as exc:
            self._update_status(item, 'failed', 'SMS status protocol unavailable: ' + str(exc))
            return
        for attempt in range(config.SMS_MAX_RETRIES + 1):
            try:
                if self._do_send(item):
                    break
                self._update_status(item, 'failed', 'Command rejected')
                return
            except urllib.error.HTTPError as exc:
                # A full queue is a definite rejection; retrying cannot duplicate a send.
                if exc.code not in (429, 503) or attempt == config.SMS_MAX_RETRIES:
                    self._update_status(item, 'failed', str(exc))
                    return
                if self._stop.wait(config.SMS_RETRY_BACKOFF_S[min(attempt, len(config.SMS_RETRY_BACKOFF_S)-1)]):
                    return
            except Exception:
                break  # ambiguous acceptance: poll the same ID, never submit again
        deadline = time.monotonic() + 90
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                ack = json.loads(self._request('/sms/status?id=' + item['id']))
                if ack.get('boot_id') != boot:
                    self._update_status(item, 'unknown', 'ESP32 restarted before acknowledgement')
                    return
                if ack.get('id') == item['id'] and ack.get('status') in ('queued', 'sending', 'sent', 'failed', 'unknown'):
                    self.receive_ack('SMSACK:' + json.dumps(ack))
                    if ack['status'] in ('sent', 'failed', 'unknown'):
                        return
            except Exception:
                pass
            self._stop.wait(1)
        self._update_status(item, 'unknown', 'Acknowledgement timeout')

    def _sender_loop(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process(item)
            finally:
                self._queue.task_done()

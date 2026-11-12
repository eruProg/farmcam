import urllib.error

import config
from sms_broker import SmsBroker
from sms_router import SmsRouter, gsm_text, normalize_phone


class FakeBroker:
    def __init__(self):
        self.sent = []

    def send(self, to, msg):
        self.sent.append((to, msg))
        return 'id-1'

    def last_status(self):
        return {'id': 'id-1', 'status': 'sent', 'err': ''}


def _router(status_by_path, replies=None):
    calls = []

    def get_json(path):
        value = status_by_path[path.split('?')[0]]
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value

    def cmd(command, timeout, retries):
        calls.append((command, timeout, retries))
        return (replies or ['OK:queued']).pop(0)

    broker = FakeBroker()
    return SmsRouter(broker, cmd, get_json=get_json), broker, calls


def _404():
    return urllib.error.HTTPError('http://esp/sms/status', 404, 'not found', {}, None)


def test_normalize_philippine_numbers():
    assert normalize_phone('09916425163') == '+639916425163'
    assert normalize_phone('639916425163') == '+639916425163'
    assert normalize_phone('+639916425163') == '+639916425163'
    assert normalize_phone('0991 642 5163') == '+639916425163'
    assert normalize_phone('') == ''
    assert gsm_text('Fungus — high ✓ 5%') == 'Fungus high 5%'


def test_v22_firmware_gets_plain_sms_once_without_a_phone_setting(monkeypatch):
    monkeypatch.setattr(config, 'FARMER_PHONE', '')
    router, broker, calls = _router({'/sms/status': _404(), '/status': {'sms_result': 'none', 'sms_queued': 0}})
    result = router.send('FarmCam L1 PUMP: Sprayed 2s')
    assert result['ok'] and result['via'] == 'v2'
    assert calls == [('SMS:FarmCam L1 PUMP: Sprayed 2s', 15.0, 0)]   # never retried
    assert broker.sent == []


def test_v3_firmware_uses_the_broker_with_a_normalized_number(monkeypatch):
    monkeypatch.setattr(config, 'FARMER_PHONE', '09916425163')
    router, broker, calls = _router({'/sms/status': {'id': 'probe', 'boot_id': 'b1', 'status': 'unknown'}})
    result = router.send('hello')
    assert result['ok'] and result['via'] == 'v3'
    assert broker.sent == [('+639916425163', 'hello')] and calls == []
    monkeypatch.setattr(config, 'FARMER_PHONE', '')
    router._firmware = None
    assert router.send('hello')['err'].startswith('No farmer phone')


def test_v2_status_ignores_the_previous_sms_result(monkeypatch):
    states = iter([
        {'sms_result': 'submitted to network', 'sms_queued': 0},   # before sending: old SMS
        {'sms_result': 'submitted to network', 'sms_queued': 1},   # ours is queued
        {'sms_result': 'sending', 'sms_queued': 0},
        {'sms_result': 'submitted to network', 'sms_queued': 0},   # ours went out
    ])
    router, _, _ = _router({'/sms/status': _404(), '/status': lambda: next(states)})
    router.send('test')
    assert router.status()['status'] == 'sending'
    assert router.status()['status'] == 'sending'
    assert router.status()['status'] == 'sent'


def test_v2_status_reports_the_sim_error():
    states = iter([{'sms_result': 'none', 'sms_queued': 0},
                   {'sms_result': 'failed before message submission', 'sms_queued': 0,
                    'sim_error': 'Not registered on GSM network'}])
    router, _, _ = _router({'/sms/status': _404(), '/status': lambda: next(states)})
    router.send('test')
    status = router.status()
    assert status['status'] == 'failed' and 'GSM' in status['err']


def test_unreachable_esp32_does_not_claim_success():
    router, _, calls = _router({'/sms/status': OSError('timeout'), '/status': OSError('timeout')},
                               replies=[None])
    result = router.send('test')
    assert not result['ok'] and calls and calls[0][2] == 0


def test_broker_follows_a_new_esp32_address(monkeypatch):
    monkeypatch.setattr(config, 'ESP32_IP', '10.0.0.5')
    broker = SmsBroker()
    assert broker._base == 'http://10.0.0.5:80'
    monkeypatch.setattr(config, 'ESP32_IP', '10.0.0.77')
    assert broker._base == 'http://10.0.0.77:80'

import json
from unittest.mock import Mock
from sms_broker import SmsBroker

def test_queue_is_not_sent_and_ack_is_correlated():
    broker = SmsBroker()
    mid = broker.send('+639123456789','Test')
    assert broker.last_status()['status'] == 'queued'
    broker.receive_ack('SMSACK:{"id":"other","status":"sent"}')
    broker.receive_ack('SMSACK:invalid')
    assert broker.last_status()['status'] == 'queued'
    broker.receive_ack('SMSACK:' + json.dumps(dict(id=mid,status='sent')))
    assert broker.last_status()['status'] == 'sent'
    assert broker.last_status()['sent_at'] is not None

def test_lost_submit_ack_polls_without_duplicate_send():
    broker = SmsBroker()
    mid = broker.send('+639123456789','Test')
    item = broker._queue.get_nowait()
    broker._request = Mock(side_effect=[json.dumps(dict(id=mid,boot_id='boot',status='unknown')),
        TimeoutError(),json.dumps(dict(id=mid,boot_id='boot',status='sent'))])
    broker._process(item)
    assert broker.last_status()['status'] == 'sent'
    assert len([call for call in broker._request.call_args_list if call.args[0] == '/cmd']) == 1

def test_reboot_is_unknown_not_retried():
    broker = SmsBroker()
    mid = broker.send('+639123456789','Test')
    item = broker._queue.get_nowait()
    broker._request = Mock(side_effect=[json.dumps(dict(id=mid,boot_id='a',status='unknown')),
        'OK:queued:'+mid,json.dumps(dict(id=mid,boot_id='b',status='unknown'))])
    broker._process(item)
    assert broker.last_status()['status'] == 'unknown'

def test_queue_full_returns_failure():
    broker = SmsBroker()
    for _ in range(20):
        assert broker.send('+639123456789','Test')
    assert broker.send('+639123456789','Test') == ''

def test_queue_rejection_retries_same_id_with_backoff():
    import urllib.error
    broker = SmsBroker()
    mid = broker.send('+639123456789', 'Test')
    item = broker._queue.get_nowait()
    broker._stop = Mock()
    broker._stop.wait.return_value = False
    broker._stop.is_set.return_value = False
    broker._request = Mock(side_effect=[
        json.dumps(dict(id=mid, boot_id='boot', status='unknown')),
        urllib.error.HTTPError('/cmd', 503, 'Queue full', {}, None),
        'OK:queued:' + mid,
        json.dumps(dict(id=mid, boot_id='boot', status='sent')),
    ])
    broker._process(item)
    submissions = [c for c in broker._request.call_args_list if c.args[0] == '/cmd']
    assert len(submissions) == 2
    assert submissions[0].args[1] == submissions[1].args[1]
    broker._stop.wait.assert_called_once_with(2)
    assert broker.last_status()['status'] == 'sent'

def test_text_mode_excludes_unicode_escapes_and_uart_control_bytes():
    broker = SmsBroker()
    broker.send('+639123456789', 'Leaf \u2014 "alert"\x1a\\')
    item = broker._queue.get_nowait()
    assert item['msg'] == 'Leaf   "alert" \\'

def test_ack_timeout_is_unknown(monkeypatch):
    import sms_broker
    broker = SmsBroker()
    mid = broker.send('+639123456789', 'Test')
    item = broker._queue.get_nowait()
    broker._request = Mock(side_effect=[
        json.dumps(dict(id=mid, boot_id='boot', status='unknown')),
        'OK:queued:' + mid,
    ])
    clock = iter([0, 91])
    monkeypatch.setattr(sms_broker.time, 'monotonic', lambda: next(clock))
    broker._process(item)
    assert broker.last_status()['status'] == 'unknown'

"""Hardware-free regression tests for the server's actual control functions.

Load just these definitions to avoid starting camera/model/database services.
Run: python -m unittest discover -s rpi_server -p 'test_*.py'
"""
import ast
import asyncio
import re
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch


class Reply:
    def __init__(self, status_code, content):
        self.status_code, self.content = status_code, content


class PumpControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.thread = Mock()
        self.command = Mock(return_value='OK')
        self.cooldown = Mock()      # CooldownManager stand-in; inactive by default
        self.cooldown.status.return_value = {'active': False, 'remaining_s': 0.0, 'total_s': 604800,
                                             'last_triggered_at': None}
        self.ns = dict(
            coordinator=types.SimpleNamespace(get_shared_state=lambda: {'cooldown_mgr': self.cooldown},
                                              notify=Mock()),
            config=types.SimpleNamespace(PUMP_COOLDOWN_S=7 * 86400),
            JSONResponse=Reply, threading=types.SimpleNamespace(Thread=self.thread),
            time=types.SimpleNamespace(time=time.time, sleep=Mock()), datetime=datetime,
            pump_lock=threading.Lock(), manual_control_lock=threading.Lock(),
            pump_active=False, manual_pump_on=False, manual_pump_heartbeat=0.0,
            manual_pump_generation=0, _manual_pump_start_time=0.0,
            MANUAL_PUMP_TIMEOUT=2.0, ESP32_IP='192.0.2.1', ESP32_PORT=80,
            LAST_PUMP_FILE=str(Path(self.temp.name) / 'last.txt'), active_station_id=1,
            sms_lock=threading.Lock(), sms_cooldown={}, SMS_COOLDOWN_SECS=10,
            esp32_cmd=self.command, send_sms_alert=Mock(), re=re,
        )
        source = ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8-sig'))
        names = {'api_pump_hold', 'api_pump_release', 'api_pump_heartbeat', 'api_pump_trigger',
                 '_manual_pump_watchdog', '_pump_worker', 'esp32_cmd', 'send_sms_alert',
                 '_manual_spray_blocked', '_restart_pump_cooldown', 'duration_text', '_spray_text',
                 '_found_text', '_spray_sms'}
        nodes = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), self.ns)
        self.real_command = self.ns['esp32_cmd']
        self.real_sms = self.ns['send_sms_alert']
        self.ns['send_sms_alert'] = Mock()
        self.ns['esp32_cmd'] = self.command

    def test_unreachable_hold_returns_failure_and_does_not_fallback_to_timed_spray(self):
        self.command.return_value = None
        result = self.ns['api_pump_hold']()
        self.assertEqual(result.status_code, 503)
        self.assertFalse(self.ns['manual_pump_on'])
        self.assertFalse(self.ns['pump_active'])
        self.assertFalse(Path(self.ns['LAST_PUMP_FILE']).exists())
        self.assertEqual([c.args[0] for c in self.command.call_args_list], ['PUMP_HOLD', 'PUMP_OFF'])
        self.thread.assert_not_called()

    def test_confirmed_hold_queues_sms_once_before_release(self):
        self.assertTrue(self.ns['api_pump_hold']()['active'])
        sms_calls = [c for c in self.thread.call_args_list if c.kwargs.get('target') is self.ns['send_sms_alert']]
        self.assertEqual(len(sms_calls), 1)
        self.assertEqual(sms_calls[0].kwargs['args'][0], 'MANUAL_PUMP')
        # A long hold, duplicate hold request, and heartbeats send no more SMS.
        self.ns['api_pump_hold']()
        asyncio.run(self.ns['api_pump_heartbeat']())
        self.assertEqual(self.thread.call_count, 2)
        self.assertTrue(self.ns['api_pump_release']()['ok'])
        self.assertFalse(self.ns['manual_pump_on'])
        self.ns['api_pump_release']()
        self.assertEqual(self.thread.call_count, 2)  # watchdog + one SMS worker

    def test_manual_sms_cooldown_is_ten_seconds_even_when_forced(self):
        clock = Mock(return_value=100.0)
        self.ns['time'].time = clock
        self.real_sms('MANUAL_PUMP', 1, 'started')
        clock.return_value = 109.9
        self.real_sms('MANUAL_PUMP', 1, 'started again', force=True)
        self.assertEqual(self.command.call_count, 1)
        clock.return_value = 110.0
        self.real_sms('MANUAL_PUMP', 1, 'new press')
        self.assertEqual(self.command.call_count, 2)

    def test_manual_sms_is_not_retried_if_queue_acknowledgement_is_lost(self):
        self.command.return_value = None
        self.real_sms('MANUAL_PUMP', 1, 'started')
        self.command.assert_called_once()

    def test_failed_stop_does_not_send_success_sms(self):
        self.ns['api_pump_hold']()
        self.thread.reset_mock()
        self.command.return_value = None
        self.assertEqual(self.ns['api_pump_release']().status_code, 503)
        self.thread.assert_not_called()

    def test_manual_hold_does_not_interrupt_automatic_spray(self):
        self.ns['pump_active'] = True
        self.assertEqual(self.ns['api_pump_hold']().status_code, 409)
        self.command.assert_not_called()

    def test_old_watchdog_cannot_stop_a_new_hold(self):
        self.ns.update(manual_pump_on=True, pump_active=True, manual_pump_generation=2)
        self.ns['_manual_pump_watchdog'](1)
        self.command.assert_not_called()
        self.assertTrue(self.ns['pump_active'])

    def test_missing_app_heartbeat_stops_pump(self):
        self.ns.update(manual_pump_on=True, pump_active=True, manual_pump_generation=1)
        self.ns['_manual_pump_watchdog'](1)
        self.command.assert_called_once_with('PUMP_OFF', timeout=2.0)
        self.assertFalse(self.ns['pump_active'])

    def test_idle_heartbeat_is_reported_as_inactive(self):
        result = asyncio.run(self.ns['api_pump_heartbeat']())
        self.assertFalse(result['active'])

    def test_failed_automatic_spray_does_not_record_or_confirm_spray(self):
        self.command.return_value = None
        self.ns['_pump_worker'](2.5, 1, 'PEST')
        self.assertFalse(Path(self.ns['LAST_PUMP_FILE']).exists())
        self.thread.assert_not_called()
        self.assertFalse(self.ns['pump_active'])

    def _cooldown_running(self, remaining=4 * 86400 + 3 * 3600):
        self.cooldown.status.return_value = {'active': True, 'remaining_s': float(remaining),
                                             'total_s': 604800, 'last_triggered_at': 1000.0}

    def test_manual_hold_during_cooldown_needs_confirmation(self):
        self._cooldown_running()
        blocked = self.ns['api_pump_hold']()
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.content['error'], 'cooldown')
        self.assertEqual(blocked.content['message'], 'Automatic spraying is paused for another 4 days 3 h.')
        self.command.assert_not_called()
        self.assertTrue(self.ns['api_pump_hold'](override=True)['active'])
        self.cooldown.record_fire.assert_called_once_with('pump')      # the 7 days start again

    def test_manual_tap_during_cooldown_needs_confirmation(self):
        self._cooldown_running(3600 * 5)
        self.assertEqual(self.ns['api_pump_trigger']().status_code, 409)
        self.cooldown.record_fire.assert_not_called()
        self.assertTrue(self.ns['api_pump_trigger'](override=True)['ok'])
        self.cooldown.record_fire.assert_called_once_with('pump')

    def test_manual_spray_outside_cooldown_restarts_it_without_asking(self):
        self.assertTrue(self.ns['api_pump_trigger']()['ok'])
        self.cooldown.record_fire.assert_called_once_with('pump')

    def test_spray_messages_say_all_lettuces(self):
        text = self.ns['_spray_text']
        self.assertEqual(text(2.5, 'AUTO: 3 whiteflies on Lettuce 2'),
                         '3 whiteflies found on Lettuce 2. All lettuces were sprayed.')
        self.assertEqual(text(2.5, 'MANUAL_TRIGGER'), 'All lettuces were sprayed from the app.')
        sms = self.ns['_spray_sms']
        self.assertEqual(sms('AUTO: 3 whiteflies on Lettuce 2'),
                         'FarmCam alert: 3 whiteflies found on Lettuce 2. Spray done.')
        self.assertEqual(sms('AUTO: 4 worms on Lettuce 1'), 'FarmCam alert: 4 worms found on Lettuce 1. Spray done.')
        self.assertEqual(sms('AUTO: fungus on 18% of Lettuce 3'),
                         'FarmCam alert: Fungus found on Lettuce 3. Spray done.')
        self.assertEqual(sms('MANUAL_TRIGGER'), 'FarmCam: Lettuces sprayed from the app.')
        self.assertEqual(self.ns['duration_text'](7 * 86400), '7 days 0 h')
        self.assertEqual(self.ns['duration_text'](3 * 3600 + 120), '3 h 2 min')

    def test_http_200_error_body_is_rejected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('urllib.request.urlopen', return_value=response):
            for body in (b'ERR:unknown_cmd', b'<html>wrong device</html>', b''):
                response.read.return_value = body
                self.assertIsNone(self.real_command('PUMP_HOLD'))
            response.read.return_value = b'OK:queued\r\n'
            self.assertEqual(self.real_command('SMS:test'), 'OK:queued')


if __name__ == '__main__':
    unittest.main()

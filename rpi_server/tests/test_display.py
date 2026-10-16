import asyncio
import ast
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from display_health import DisplayHealth
from display_server import DisplayState, create_app
from install_display import ManagedFiles, autostart_text, without_block, service_text
from kiosk_runner import browser_arguments


def responses():
    return {
        '/api/display/health': dict(instance_id='boot-a', camera_frame_age_s=.2,
            detection_loop_age_s=.1, coordinator_ready=True, fault=None, pump_active=False),
        '/api/system': dict(stations=[dict(id=1,status='pest',last_scanned=100)],
            scan_state=dict(phase='DETECT_UPPER',station=1),buzzer_muted=True),
        '/api/sms/status': dict(status='sent'),
        '/api/logs': dict(items=[dict(id='track',station=1,**{'class':'worm'},status='active')]),
    }


def test_wait_start_monitor_disconnect_recover_and_restart():
    async def scenario():
        data = responses()
        offline = [True]
        clock = [0]
        async def handle(request):
            if offline[0]:
                raise httpx.ConnectError('offline', request=request)
            assert request.method == 'GET'
            return httpx.Response(200,json=data[request.url.path])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
            state = DisplayState(client,lambda:clock[0])
            await state.poll_status()
            assert state.snapshot()['status'] == 'waiting'
            offline[0] = False
            data['/api/display/health']['coordinator_ready'] = False
            await state.poll_status()
            assert state.snapshot()['status'] == 'starting'
            data['/api/display/health']['coordinator_ready'] = True
            await state.poll_status(); await state.poll_logs()
            assert state.snapshot()['status'] == 'monitoring'
            offline[0] = True
            await state.poll_status(); await state.poll_logs()
            snapshot = state.snapshot()
            assert snapshot['status'] == 'reconnecting'
            assert snapshot['stations'][0]['id'] == 1
            assert snapshot['logs'][0]['id'] == 'track'
            assert not snapshot['logs_available']
            assert not snapshot['camera_available']
            offline[0] = False
            data['/api/display/health']['instance_id'] = 'boot-b'
            await state.poll_status()
            assert state.snapshot()['instance_id'] == 'boot-b'
            clock[0] = 6
            assert state.snapshot()['status'] == 'reconnecting'
    asyncio.run(scenario())


def test_camera_and_detection_faults_and_empty_logs():
    async def scenario():
        data = responses()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,json=data[r.url.path])),base_url='http://test') as client:
            state = DisplayState(client,lambda:0)
            for changes, expected in [({'camera_frame_age_s':4},'camera_unavailable'),
                ({'camera_frame_age_s':0,'detection_loop_age_s':11},'interrupted'),
                ({'detection_loop_age_s':0,'fault':'Verifier failed'},'interrupted')]:
                data['/api/display/health'].update(changes)
                await state.poll_status()
                assert state.snapshot()['status'] == expected
            data['/api/logs'] = {'items':[]}
            await state.poll_logs()
            assert state.snapshot()['logs_available'] and state.snapshot()['logs'] == []
            data['/api/logs'] = {'items':'malformed'}
            await state.poll_logs()
            assert not state.snapshot()['logs_available']
    asyncio.run(scenario())


def test_polls_never_overlap():
    async def scenario():
        seen = []
        async def handle(request):
            seen.append(request.url.path)
            await asyncio.sleep(.01)
            return httpx.Response(200,json=responses()[request.url.path])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test') as client:
            state = DisplayState(client)
            await asyncio.gather(state.poll_status(),state.poll_status(),state.poll_logs(),state.poll_logs())
            assert len(seen) == 4
    asyncio.run(scenario())


def test_display_only_exposes_read_routes_and_local_assets():
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(503)),base_url='http://test')
    with TestClient(create_app(upstream,poll=False)) as client:
        assert client.get('/').status_code == 200
        assert client.get('/assets/Inter.ttf').status_code == 200
        assert client.get('/assets/../../main.py').status_code == 404
        assert client.get('/api/state').json()['status'] == 'waiting'
        assert client.get('/stream').status_code == 503
        for path in ('/api/pump/hold','/api/pump/release','/api/model/COMB','/api/buzzer/mute','/api/sms/test','/proxy?url=http://evil'):
            assert client.get(path).status_code == 404
            assert client.post(path).status_code == 404
        assert client.post('/api/state').status_code == 405
        assert client.get('/').headers['content-security-policy'].startswith("default-src 'self'")
    asyncio.run(upstream.aclose())


def test_stream_uses_fixed_endpoint_and_cleans_up():
    paths = []
    async def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200,headers={'content-type':'multipart/x-mixed-replace; boundary=frame'},content=b'--frame\r\n')
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://test')
    app = create_app(upstream,poll=False)
    with TestClient(app) as client:
        state = app.state.display
        state.connected = True; state.health = responses()['/api/display/health']
        state.stream_active = True
        assert client.get('/stream').status_code == 409
        state.stream_active = False
        assert client.get('/stream?url=http://evil').content == b'--frame\r\n'
        assert paths == ['/api/display/stream']
        assert not state.stream_active
    asyncio.run(upstream.aclose())


def test_health_uses_current_frames_and_worker_heartbeat():
    clock = [20]
    health = DisplayHealth(lambda:clock[0])
    assert health.snapshot(0,False,{})['detection_loop_age_s'] is None
    health.tick(); clock[0] = 24
    data = health.snapshot(23,True,dict(ready=True,pump_active=False))
    assert data['camera_frame_age_s'] == 1
    assert data['detection_loop_age_s'] == 4
    assert data['coordinator_ready']
    assert DisplayHealth().instance_id != health.instance_id


def test_installer_repeated_install_preserves_original_and_later_edits(tmp_path):
    config = tmp_path/'autostart'
    original = '# My existing startup\npanel &\n'
    config.write_text(original)
    manifest = tmp_path/'backup.json'
    for _ in range(2):
        managed = ManagedFiles(manifest)
        managed.write(config,autostart_text(config.read_text(),'python kiosk.py','labwc'),block=True)
    assert config.read_text().count('python kiosk.py') == 1
    ManagedFiles(manifest).restore()
    assert config.read_text() == original
    managed = ManagedFiles(manifest)
    managed.write(config,autostart_text(original,'python kiosk.py','labwc'),block=True)
    config.write_text(config.read_text()+'new-program &\n')
    managed.restore()
    assert config.read_text() == original+'new-program &\n'


def test_wayfire_insertion_and_new_config_removal(tmp_path):
    original = '[core]\nplugins = autostart\n[autostart]\npanel = wf-panel-pi\n[idle]\ndpms_timeout = 600\n'
    edited = autostart_text(original,'python kiosk.py','wayfire')
    assert autostart_text(edited,'python kiosk.py','wayfire') == edited
    assert without_block(edited) == original
    managed = ManagedFiles(tmp_path/'backup.json')
    target = tmp_path/'new.service'
    managed.write(target,'service')
    managed.restore()
    assert not target.exists()


def test_service_is_independent_and_browser_is_sandboxed():
    unit = service_text('farmer',PurePosixPath('/home/farmer/Farm Cam'),PurePosixPath('/home/farmer/Farm Cam/.display_env/bin/python'))
    assert 'WorkingDirectory=/home/farmer/Farm Cam\n' in unit
    assert 'Requires=farmcam' not in unit and 'main.py' not in unit
    assert 'User=farmer' in unit
    args = browser_arguments('/usr/bin/chromium','/home/farmer/profile')
    assert '--no-sandbox' not in args and '--kiosk' in args
    assert args[-1] == 'http://127.0.0.1:8090/'


def test_display_module_does_not_import_hardware():
    source = (Path(__file__).parents[1]/'display_server.py').read_text()
    imports = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node,ast.ImportFrom)]
    assert not set(imports) & {'main','coordinator','picamera2','hailo_platform','cv2'}


def test_monitor_stream_caps_size_and_rate_without_changing_mobile_default():
    import threading
    from unittest.mock import Mock
    import cv2
    import numpy as np
    source = (Path(__file__).parents[1]/'main.py').read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name == 'generate_mjpeg')
    sleep = Mock()
    scope = dict(frame_lock=threading.Lock(),current_frame=np.zeros((960,1280,3),np.uint8),
        display_detections=[],np=np,cv2=cv2,comb_model_loaded=False,seg_model_loaded=False,
        det_model_loaded=False,_infer_debug={},current_state='DETECT_A',active_station_id=1,
        time=SimpleNamespace(sleep=sleep))
    exec(compile(ast.Module(body=[node],type_ignores=[]),'stream-test','exec'),scope)
    for kwargs, expected_width, expected_delay in [({},1280,.066),({'max_width':960,'fps':8,'show_diagnostics':False},960,.125)]:
        stream = scope['generate_mjpeg'](**kwargs)
        raw = next(stream).split(b'\r\n\r\n',1)[1].rstrip(b'\r\n')
        assert cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR).shape[1] == expected_width
        next(stream)
        sleep.assert_called_with(expected_delay)
        stream.close()


def test_no_backend_stream_failure_latches_active_flag():
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(503)),base_url='http://test')
    app = create_app(upstream,poll=False)
    with TestClient(app) as client:
        state = app.state.display
        state.connected = True; state.health = responses()['/api/display/health']
        for _ in range(2):
            assert client.get('/stream').status_code == 503
            assert not state.stream_active
    asyncio.run(upstream.aclose())


def test_cached_scan_time_advances_without_changing_phase_or_original():
    state = DisplayState(None,lambda:102)
    state.connected = True; state.observed_at = 100; state.scan_observed_at = 100
    state.health = responses()['/api/display/health']
    state.system = {'scan_state':{'server_time':100,'phase_started_at':90,'phase_duration_s':30}}
    assert state.snapshot()['scan']['server_time'] == 102
    assert state.system['scan_state']['server_time'] == 100
    assert state.snapshot()['camera_frame_age_s'] == 2.2


def test_cancelled_stream_releases_upstream_and_single_connection_slot():
    async def scenario():
        class Frames(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'frame'
                await asyncio.Event().wait()
            async def aclose(self):
                await asyncio.sleep(0)
                self.closed = True
        frames = Frames()
        transport = httpx.MockTransport(lambda r:httpx.Response(200,stream=frames,
            headers={'content-type':'multipart/x-mixed-replace; boundary=frame'}))
        async with httpx.AsyncClient(transport=transport,base_url='http://test') as upstream:
            app = create_app(upstream,poll=False)
            async with app.router.lifespan_context(app):
                state = app.state.display
                state.connected = True; state.health = responses()['/api/display/health']
                endpoint = next(r.endpoint for r in app.routes if r.path == '/stream')
                response = await endpoint()
                assert await anext(response.body_iterator) == b'frame'
                pending = asyncio.create_task(anext(response.body_iterator))
                await asyncio.sleep(0)
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
                assert frames.closed
                assert not state.stream_active
    asyncio.run(scenario())

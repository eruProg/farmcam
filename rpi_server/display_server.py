"""Loopback-only HDMI viewer. Reads fixed API routes; never imports main.py."""
import asyncio
import math
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import time

import httpx
from display_stream import SharedCameraStream
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'assets' if (ROOT / 'assets').exists() else ROOT.parent / 'assets'
UPSTREAM = 'http://127.0.0.1:8000'


class DisplayState:
    def __init__(self, client, clock=time.monotonic):
        self.client = client
        self.clock = clock
        self.ever_connected = False
        self.connected = False
        self.health = None
        self.system = None
        self.sms = None
        self.logs = []
        self.logs_available = False
        self.updated_at = None
        self.observed_at = None
        self.scan_observed_at = None
        self.logs_updated_at = None
        self.relay = SharedCameraStream(client, clock)
        self._status_lock = asyncio.Lock()
        self._logs_lock = asyncio.Lock()

    async def _get(self, path):
        try:
            response = await self.client.get(path)
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    async def poll_status(self):
        if self._status_lock.locked():
            return
        async with self._status_lock:
            async def timed_get(path):
                data = await self._get(path)
                return data, self.clock()
            health_result, system_result, sms = await asyncio.gather(
                timed_get('/api/display/health'), timed_get('/api/system'), self._get('/api/sms/status'))
            health, observed_at = health_result
            system, scan_observed_at = system_result
            valid_health = health is not None and isinstance(health.get('instance_id'), str)
            self.connected = bool(valid_health and system is not None and isinstance(system.get('stations'), list)
                                  and all(isinstance(row, dict) for row in system['stations']))
            if self.connected:
                self.health, self.system, self.sms = health, system, sms
                self.updated_at = time.time()
                self.observed_at = observed_at
                self.scan_observed_at = scan_observed_at
                self.ever_connected = True
            else:
                self.sms = None

    async def poll_logs(self):
        if self._logs_lock.locked():
            return
        async with self._logs_lock:
            data = await self._get('/api/logs?page=1&page_size=5&is_fp=0')
            self.logs_available = (data is not None and isinstance(data.get('items'), list)
                                   and all(isinstance(row, dict) for row in data['items']))
            if self.logs_available:
                self.logs = data['items'][:5]
                self.logs_updated_at = time.time()

    def snapshot(self):
        health = self.health or {}
        elapsed = max(0, self.clock() - self.observed_at) if self.observed_at is not None else 0
        connected = self.connected and elapsed < 5
        camera_age = health.get('camera_frame_age_s')
        loop_age = health.get('detection_loop_age_s')
        camera_age = camera_age + elapsed if isinstance(camera_age, (int, float)) and math.isfinite(camera_age) and camera_age >= 0 else None
        loop_age = loop_age + elapsed if isinstance(loop_age, (int, float)) and math.isfinite(loop_age) and loop_age >= 0 else None
        if not connected:
            status = 'reconnecting' if self.ever_connected else 'waiting'
        elif health.get('fault') or (loop_age is not None and loop_age >= 10):
            status = 'interrupted'
        elif not health.get('coordinator_ready') or loop_age is None:
            status = 'starting'
        elif camera_age is None or camera_age > 3:
            status = 'camera_unavailable'
        else:
            status = 'monitoring'
        relay = self.relay
        stream_age = max(0, self.clock() - relay.received_at) if relay.received_at is not None else None
        # New backends carry capture freshness independently of status requests.
        # Older backends safely fall back to their health timestamp.
        video_camera_age = (max(0, self.clock() - relay.captured_at)
                            if relay.captured_at is not None else camera_age)
        video_available = (stream_age is not None and stream_age <= 3
                           and video_camera_age is not None and video_camera_age <= 3)
        system = self.system or {}
        scan = system.get('scan_state')
        scan = dict(scan) if isinstance(scan, dict) else None
        if scan and self.scan_observed_at is not None and isinstance(scan.get('server_time'), (int, float)):
            # API state is cached between polls: advance its monotonic timestamp
            # so the browser's clock offset does not inherit that cache delay.
            scan['server_time'] += max(0, self.clock() - self.scan_observed_at)
        # Return a narrow display contract, not internal paths/errors or phone details.
        return {
            'status': status, 'connected': connected,
            'instance_id': health.get('instance_id'), 'updated_at': self.updated_at,
            'camera_available': connected and camera_age is not None and camera_age <= 3,
            'camera_frame_age_s': camera_age,
            'scan': scan,
            'stations': system.get('stations', []),
            'pump_active': health.get('pump_active') if connected else None,
            'buzzer_muted': system.get('buzzer_muted') if connected else None,
            'cooldowns': system.get('cooldowns') if connected else None,
            'sms_status': (self.sms or {}).get('status'),
            'logs': self.logs, 'logs_available': self.logs_available,
            'logs_updated_at': self.logs_updated_at,
            'stream_active': relay.active,
            'stream_age_s': stream_age,
            'video_available': video_available,
            'video_camera_age_s': video_camera_age,
            'stream_error': relay.error,
            'stream_reconnects': relay.reconnects,
            'stream_viewers': relay.viewers,
        }


async def poll_forever(callback, interval):
    while True:
        try:
            await callback()
        except Exception:
            # A malformed upstream response must not kill the local waiting screen.
            import logging
            logging.exception('Display polling failed')
        await asyncio.sleep(interval)


def create_app(client=None, poll=True):
    @asynccontextmanager
    async def lifespan(app):
        owns_client = client is None
        upstream = client or httpx.AsyncClient(base_url=UPSTREAM, trust_env=False,
            timeout=httpx.Timeout(1.5, connect=1.0), follow_redirects=False)
        state = DisplayState(upstream)
        app.state.display = state
        tasks = [asyncio.create_task(poll_forever(state.poll_status, 2)),
                 asyncio.create_task(poll_forever(state.poll_logs, 5))] if poll else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await state.relay.close()
            if owns_client:
                await upstream.aclose()

    app = FastAPI(title='FarmCam HDMI Display', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'display' / 'index.html')

    # Explicit files only: there is no general filesystem or upstream proxy route.
    files = {'display.css': ROOT/'display'/'display.css', 'display.js': ROOT/'display'/'display.js',
             'Inter.ttf': ASSETS/'fonts'/'Inter.ttf', 'logo.svg': ASSETS/'logo.svg'}

    @app.get('/assets/{name}')
    async def asset(name: str):
        if name not in files or not files[name].exists():
            raise HTTPException(404)
        return FileResponse(files[name])

    @app.get('/api/state')
    async def state():
        return app.state.display.snapshot()

    @app.get('/stream')
    async def stream():
        state = app.state.display
        # Each viewer gets the latest complete JPEG from ONE upstream reader.
        # Browser reloads never own or cancel that shared connection.
        if not state.ever_connected and not state.connected and not state.relay.active:
            raise HTTPException(503, 'Waiting for backend')
        state.relay.start()
        return StreamingResponse(state.relay.frames(),
            media_type='multipart/x-mixed-replace; boundary=frame')

    return app


app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8090, log_level='warning')

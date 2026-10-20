"""One bounded MJPEG relay, independent of browser lifetimes and hardware."""
import asyncio
import math
import re
import time
from contextlib import suppress

import httpx

MAX_FRAME = 2 * 1024 * 1024
MAX_HEADERS = 8192


class MultipartFrames:
    def __init__(self, content_type):
        match = re.search(r'boundary="?([^";\s]+)', content_type, re.I)
        if not content_type.lower().startswith('multipart/x-mixed-replace') or not match:
            raise ValueError('Invalid camera stream content type')
        boundary = match[1].encode('ascii')
        if not 1 <= len(boundary) <= 100:
            raise ValueError('Invalid camera boundary')
        self.boundary = b'--' + boundary
        self.buffer = bytearray()

    def feed(self, chunk):
        if len(self.buffer) + len(chunk) > MAX_FRAME + MAX_HEADERS + 256:
            raise ValueError('Camera frame exceeds relay buffer limit')
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(self.boundary)
            if start < 0:
                if len(self.buffer) > MAX_HEADERS:
                    raise ValueError('Camera boundary missing')
                break
            if start:
                del self.buffer[:start]
            headers_end = self.buffer.find(b'\r\n\r\n')
            if headers_end < 0:
                if len(self.buffer) > MAX_HEADERS:
                    raise ValueError('Camera headers too large')
                break
            if headers_end > MAX_HEADERS:
                raise ValueError('Camera headers too large')
            end = self.buffer.find(b'\r\n' + self.boundary, headers_end + 4)
            if end < 0:
                break
            headers = {}
            for line in bytes(self.buffer[len(self.boundary):headers_end]).split(b'\r\n'):
                key, sep, value = line.partition(b':')
                if sep:
                    headers[key.strip().lower()] = value.strip()
            jpeg = bytes(self.buffer[headers_end + 4:end])
            del self.buffer[:end + 2]
            if len(jpeg) > MAX_FRAME or not (jpeg.startswith(b'\xff\xd8') and jpeg.endswith(b'\xff\xd9')):
                raise ValueError('Incomplete JPEG camera frame')
            # Timestamp is produced by the local backend in the same host clock.
            raw = headers.get(b'x-capture-monotonic')
            captured = float(raw) if raw is not None else None
            if captured is not None and (not math.isfinite(captured) or captured < 0):
                raise ValueError('Invalid capture timestamp')
            frames.append((jpeg, captured))
        return frames


class SharedCameraStream:
    def __init__(self, client, clock=time.monotonic, sleep=asyncio.sleep):
        self.client, self.clock, self.sleep = client, clock, sleep
        self.task = None
        self.latest = None
        self.sequence = 0
        self.received_at = None
        self.captured_at = None
        self.active = False
        self.error = None
        self.reconnects = 0
        self.viewers = 0
        self.changed = asyncio.Event()

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    def publish(self, jpeg, captured_at):
        self.latest = jpeg
        self.captured_at = captured_at
        self.received_at = self.clock()
        self.sequence += 1
        self.error = None
        self.changed.set()
        self.changed = asyncio.Event()

    async def run(self):
        delay = 1
        while True:
            try:
                async with self.client.stream('GET', '/api/display/stream',
                        timeout=httpx.Timeout(8, connect=2)) as response:
                    response.raise_for_status()
                    parser = MultipartFrames(response.headers.get('content-type', ''))
                    self.active = True
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        for jpeg, captured in parser.feed(chunk):
                            self.publish(jpeg, captured)
                            delay = 1
                    self.error = 'Camera stream ended'
            except (httpx.HTTPError, ValueError) as exc:
                # No private upstream URL or response body in the display API.
                self.error = type(exc).__name__
            finally:
                self.active = False
            self.reconnects += 1
            await self.sleep(delay)
            delay = min(delay * 2, 15)

    async def frames(self):
        self.viewers += 1
        seen = -1
        try:
            while True:
                changed = self.changed
                age = self.clock() - self.received_at if self.received_at is not None else float('inf')
                capture_age = self.clock() - self.captured_at if self.captured_at is not None else age
                if self.latest is not None and self.sequence != seen and age <= 3 and capture_age <= 3:
                    seen = self.sequence
                    yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                           str(len(self.latest)).encode() + b'\r\n\r\n' + self.latest + b'\r\n')
                await changed.wait()
        finally:
            self.viewers -= 1

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.active = False
        self.latest = None

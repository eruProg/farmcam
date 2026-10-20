import asyncio
from contextlib import suppress

import httpx
import pytest

from display_server import create_app, DisplayState
from display_stream import MultipartFrames, SharedCameraStream, MAX_FRAME


JPEG = b'\xff\xd8test\xff\xd9'


def part(jpeg=JPEG, captured=10):
    return (b'--frame\r\nContent-Type: image/jpeg\r\nX-Capture-Monotonic: ' +
            str(captured).encode() + b'\r\n\r\n' + jpeg + b'\r\n')


def test_parser_fragmentation_complete_frames_and_bounded_memory():
    parser = MultipartFrames('multipart/x-mixed-replace; boundary="frame"')
    results = []
    for byte in part() + b'--frame\r\n':
        results.extend(parser.feed(bytes([byte])))
    assert results == [(JPEG, 10)]
    assert len(parser.buffer) < 100
    with pytest.raises(ValueError, match='buffer limit'):
        parser.feed(b'x' * (MAX_FRAME + 9000))
    with pytest.raises(ValueError, match='Incomplete JPEG'):
        MultipartFrames('multipart/x-mixed-replace; boundary=frame').feed(part(b'bad') + b'--frame\r\n')


def test_shared_viewers_slow_viewer_and_cancellation_do_not_close_upstream():
    async def scenario():
        paths, closed = [], []
        gate = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield part() + b'--frame\r\n'
                await gate.wait()
            async def aclose(self):
                closed.append(True)
        async def handle(request):
            paths.append(request.url.path)
            return httpx.Response(200, stream=Stream(), headers={
                'content-type': 'multipart/x-mixed-replace; boundary=frame'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            app = create_app(client, poll=False)
            async with app.router.lifespan_context(app):
                state = app.state.display
                state.connected = True
                state.relay.clock = lambda: 10
                endpoint = next(route.endpoint for route in app.routes if route.path == '/stream')
                a, b = await endpoint(), await endpoint()
                # A transport chunk large enough for the relay's bounded reader.
                # httpx may buffer short input until the next 16K or EOF, so use
                # a direct publish to test independent subscriber scheduling.
                state.relay.publish(JPEG, 10)
                assert JPEG in await anext(a.body_iterator)
                assert JPEG in await anext(b.body_iterator)
                await asyncio.sleep(0)
                assert paths == ['/api/display/stream']
                for n in range(20):
                    state.relay.publish(b'\xff\xd8' + str(n).encode() + b'\xff\xd9', 10)
                assert b'19' in await anext(b.body_iterator)
                assert state.relay.sequence == 21
                await a.body_iterator.aclose()
                assert not closed and state.relay.viewers == 1
                await b.body_iterator.aclose()
                assert state.relay.viewers == 0
            assert closed == [True]
            assert state.relay.latest is None
    asyncio.run(scenario())


def test_reconnect_backoff_and_fresh_frame_resets_delay():
    async def scenario():
        waits, requests = [], []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield part() + b'--frame\r\n'
        async def handle(request):
            requests.append(request.url.path)
            if len(requests) == 4:
                return httpx.Response(200, stream=Stream(), headers={'content-type':
                    'multipart/x-mixed-replace; boundary=frame'})
            raise httpx.ConnectError('offline')
        async def sleep(delay):
            waits.append(delay)
            if len(waits) == 10:
                raise asyncio.CancelledError
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://test') as client:
            relay = SharedCameraStream(client, lambda:10, sleep)
            with suppress(asyncio.CancelledError):
                await relay.run()
            assert waits == [1, 2, 4, 1, 2, 4, 8, 15, 15, 15]
            assert relay.sequence == 1
            assert not relay.active
    asyncio.run(scenario())


def test_health_failure_does_not_hide_fresh_capture_but_duplicates_are_stale():
    clock = [10]
    state = DisplayState(None, lambda:clock[0])
    state.relay.publish(JPEG, 10)
    assert not state.snapshot()['connected']
    assert state.snapshot()['video_available']
    clock[0] = 14
    state.relay.publish(JPEG, 10)  # stream still encodes the OLD captured frame
    snapshot = state.snapshot()
    assert snapshot['stream_age_s'] == 0
    assert snapshot['video_camera_age_s'] == 4
    assert not snapshot['video_available']


def test_old_backend_without_frame_timestamp_uses_health_and_expires():
    clock = [10]
    state = DisplayState(None, lambda:clock[0])
    state.connected = True
    state.observed_at = 10
    state.health = {'camera_frame_age_s': .1}
    state.relay.publish(JPEG, None)
    assert state.snapshot()['video_available']
    clock[0] = 14
    state.relay.publish(JPEG, None)
    assert not state.snapshot()['video_available']


def test_subscriber_never_sends_stale_cached_frame():
    async def scenario():
        relay = SharedCameraStream(None, lambda:10)
        relay.publish(JPEG, 2)
        frames = relay.frames()
        waiting = asyncio.create_task(anext(frames))
        await asyncio.sleep(0)
        assert not waiting.done()
        relay.publish(JPEG, 10)
        assert JPEG in await asyncio.wait_for(waiting, 1)
        await frames.aclose()
    asyncio.run(scenario())

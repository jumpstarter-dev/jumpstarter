import base64
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest
from click.testing import CliRunner
from PIL import Image

from jumpstarter_driver_ustreamer.client import (
    LANDING_PAGE,
    UStreamerClient,
    _parse_content_type,
    _proxy_mjpeg_stream,
    _run_server,
)
from jumpstarter_driver_ustreamer.common import UStreamerState


def _make_client():
    client = object.__new__(UStreamerClient)
    client.description = None
    client.methods_description = {}
    client.stack = MagicMock()
    return client


def _make_state():
    return UStreamerState.model_validate(
        {
            "ok": True,
            "result": {
                "source": {
                    "online": True,
                    "desired_fps": 30,
                    "captured_fps": 29,
                    "resolution": {"width": 1280, "height": 720},
                },
                "encoder": {"type": "CPU", "quality": 85},
            },
        }
    )


def _make_jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 1), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _get_route_handler(app, path):
    for resource in app.router.resources():
        if getattr(resource, "canonical", None) == path:
            return next(iter(resource)).handler
    raise AssertionError(f"Route {path} was not registered")


class _FakeStreamContext:
    def __init__(self, tunnel):
        self.tunnel = tunnel

    async def __aenter__(self):
        return self.tunnel

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeTunnel:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def receive(self):
        chunk = next(self._chunks)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk


class _FakeStreamResponse:
    def __init__(self):
        self.content_type = None
        self.prepared_request = None
        self.writes = []

    async def prepare(self, request):
        self.prepared_request = request

    async def write(self, data):
        self.writes.append(data)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_parse_content_type_returns_header_value():
    header_bytes = b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nX-Test: 1\r\n"

    assert _parse_content_type(header_bytes) == "image/jpeg"


def test_parse_content_type_falls_back_to_mjpeg_default():
    assert _parse_content_type(b"HTTP/1.1 200 OK\r\nX-Test: 1\r\n") == "multipart/x-mixed-replace; boundary=--"


def test_state_returns_validated_model():
    client = _make_client()
    client.call = MagicMock(return_value=_make_state().model_dump(mode="python"))

    state = UStreamerClient.state(client)

    assert state == _make_state()
    client.call.assert_called_once_with("state")


def test_snapshot_returns_image():
    client = _make_client()
    jpeg_bytes = _make_jpeg_bytes()
    client.call = MagicMock(return_value=base64.b64encode(jpeg_bytes).decode("ascii"))

    image = UStreamerClient.snapshot(client)

    assert image.size == (2, 1)
    client.call.assert_called_once_with("snapshot")


def test_snapshot_bytes_returns_decoded_jpeg():
    client = _make_client()
    jpeg_bytes = _make_jpeg_bytes()
    client.call = MagicMock(return_value=base64.b64encode(jpeg_bytes).decode("ascii"))

    assert UStreamerClient.snapshot_bytes(client) == jpeg_bytes
    client.call.assert_called_once_with("snapshot")


def test_state_command_prints_source_and_encoder_details():
    client = _make_client()
    client.state = MagicMock(return_value=_make_state())

    result = CliRunner().invoke(client.cli(), ["state"])

    assert result.exit_code == 0
    assert "Online:     True" in result.output
    assert "Resolution: 1280x720" in result.output
    assert "FPS:        29/30" in result.output
    assert "Encoder:    CPU (quality: 85)" in result.output


def test_snapshot_command_saves_snapshot_to_requested_path():
    client = _make_client()
    image = MagicMock()
    client.snapshot = MagicMock(return_value=image)

    result = CliRunner().invoke(client.cli(), ["snapshot", "--output", "frame.jpg"])

    assert result.exit_code == 0
    image.save.assert_called_once_with("frame.jpg")
    assert "Saved snapshot to frame.jpg" in result.output


def test_stream_command_registers_routes_and_starts_server():
    client = _make_client()
    client.call_async = AsyncMock(return_value=base64.b64encode(b"jpeg-data").decode("ascii"))

    captured = {}
    proxied_response = object()

    with (
        patch(
            "jumpstarter_driver_ustreamer.client._run_server",
            side_effect=lambda client_arg, app, port, browser: captured.update(
                {"client": client_arg, "app": app, "port": port, "browser": browser}
            ),
        ),
        patch(
            "jumpstarter_driver_ustreamer.client._proxy_mjpeg_stream",
            new=AsyncMock(return_value=proxied_response),
        ) as mock_proxy,
    ):
        result = CliRunner().invoke(client.cli(), ["stream", "--port", "1234", "--no-browser"])

        assert result.exit_code == 0
        assert captured["client"] is client
        assert captured["port"] == 1234
        assert captured["browser"] is False

        async def exercise_routes():
            app = captured["app"]
            index_handler = _get_route_handler(app, "/")
            snapshot_handler = _get_route_handler(app, "/snapshot")
            stream_handler = _get_route_handler(app, "/stream")

            index_response = await index_handler(object())
            assert index_response.text == LANDING_PAGE

            snapshot_response = await snapshot_handler(object())
            assert snapshot_response.body == b"jpeg-data"
            assert snapshot_response.content_type == "image/jpeg"

            request = object()
            response = await stream_handler(request)
            assert response is proxied_response
            mock_proxy.assert_awaited_once_with(client, request)

        anyio.run(exercise_routes)

    client.call_async.assert_awaited_once_with("snapshot")


def test_run_server_uses_public_site_port_and_cleans_up():
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock(), addresses=[("127.0.0.1", 59172)])
    site = SimpleNamespace(start=AsyncMock())
    client = SimpleNamespace(portal=SimpleNamespace(call=lambda fn, *args: anyio.run(fn, *args)))

    async def raise_keyboard_interrupt():
        raise KeyboardInterrupt

    with (
        patch("jumpstarter_driver_ustreamer.client.web.AppRunner", return_value=runner),
        patch("jumpstarter_driver_ustreamer.client.web.TCPSite", return_value=site),
        patch(
            "jumpstarter_driver_ustreamer.client.move_on_after",
            side_effect=lambda *args, **kwargs: contextlib.nullcontext(),
        ),
        patch("anyio.sleep_forever", new=raise_keyboard_interrupt),
        patch("jumpstarter_driver_ustreamer.client.webbrowser.open") as mock_open,
        patch("jumpstarter_driver_ustreamer.client.click.echo") as mock_echo,
    ):
        _run_server(client, object(), 0, True)

    runner.setup.assert_awaited_once()
    site.start.assert_awaited_once()
    runner.cleanup.assert_awaited_once()
    mock_open.assert_called_once_with("http://127.0.0.1:59172")
    mock_echo.assert_any_call("Video stream available at: http://127.0.0.1:59172")
    mock_echo.assert_any_call("Snapshot endpoint: http://127.0.0.1:59172/snapshot")
    mock_echo.assert_any_call("Press Ctrl+C to stop.")
    mock_echo.assert_any_call("\nStopping video server.")


def test_run_server_propagates_startup_errors():
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock(), addresses=[])
    site = SimpleNamespace(start=AsyncMock(side_effect=OSError("port in use")))
    client = SimpleNamespace(portal=SimpleNamespace(call=lambda fn, *args: anyio.run(fn, *args)))

    with (
        patch("jumpstarter_driver_ustreamer.client.web.AppRunner", return_value=runner),
        patch("jumpstarter_driver_ustreamer.client.web.TCPSite", return_value=site),
        patch(
            "jumpstarter_driver_ustreamer.client.move_on_after",
            side_effect=lambda *args, **kwargs: contextlib.nullcontext(),
        ),
        patch("jumpstarter_driver_ustreamer.client.click.echo") as mock_echo,
    ):
        with pytest.raises(OSError, match="port in use"):
            _run_server(client, object(), 0, False)

    runner.setup.assert_awaited_once()
    site.start.assert_awaited_once()
    runner.cleanup.assert_awaited_once()
    mock_echo.assert_not_called()


def test_run_server_raises_when_no_bound_address_is_reported():
    runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock(), addresses=[])
    site = SimpleNamespace(start=AsyncMock())
    client = SimpleNamespace(portal=SimpleNamespace(call=lambda fn, *args: anyio.run(fn, *args)))

    with (
        patch("jumpstarter_driver_ustreamer.client.web.AppRunner", return_value=runner),
        patch("jumpstarter_driver_ustreamer.client.web.TCPSite", return_value=site),
        patch(
            "jumpstarter_driver_ustreamer.client.move_on_after",
            side_effect=lambda *args, **kwargs: contextlib.nullcontext(),
        ),
    ):
        with pytest.raises(RuntimeError, match="without a bound address"):
            _run_server(client, object(), 0, False)

    runner.setup.assert_awaited_once()
    site.start.assert_awaited_once()
    runner.cleanup.assert_awaited_once()


@pytest.mark.anyio
async def test_proxy_mjpeg_stream_forwards_headers_and_body_chunks():
    tunnel = _FakeTunnel(
        [
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                b"\r\n"
                b"--frame-1"
            ),
            b"--frame-2",
            anyio.EndOfStream(),
        ]
    )
    client = SimpleNamespace(stream_async=lambda method: _FakeStreamContext(tunnel))
    response = _FakeStreamResponse()
    request = object()

    with patch("jumpstarter_driver_ustreamer.client.web.StreamResponse", return_value=response):
        result = await _proxy_mjpeg_stream(client, request)

    assert result is response
    assert tunnel.sent == [b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n"]
    assert response.content_type == "multipart/x-mixed-replace; boundary=frame"
    assert response.prepared_request is request
    assert response.writes == [b"--frame-1", b"--frame-2"]

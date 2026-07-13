import io
import webbrowser
from base64 import b64decode

import click
from aiohttp import web
from anyio import EndOfStream, get_cancelled_exc_class, move_on_after
from PIL import Image

from .common import UStreamerState
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

LANDING_PAGE = """\
<!DOCTYPE html>
<html>
<head>
  <title>Video</title>
  <style>
    body { background: #1a1a1a; color: #eee; font-family: system-ui; margin: 0;
           display: flex; flex-direction: column; align-items: center; padding: 20px; }
    img { max-width: 100%; border: 1px solid #333; }
    a { color: #6cf; }
    .info { margin: 10px 0; font-size: 14px; color: #aaa; }
  </style>
</head>
<body>
  <h2>Jumpstarter Video Stream</h2>
  <img src="/stream" alt="Live video stream" />
  <p class="info"><a href="/snapshot">Single snapshot (JPEG)</a></p>
</body>
</html>
"""


def _parse_content_type(header_bytes: bytes) -> str:
    """Extract Content-Type from raw HTTP response headers."""
    for line in header_bytes.decode("ascii", errors="replace").split("\r\n"):
        if line.lower().startswith("content-type:"):
            return line.split(":", 1)[1].strip()
    return "multipart/x-mixed-replace; boundary=--"


def _run_server(client, app, port, open_browser):
    """Run an aiohttp app, opening the browser and blocking until Ctrl+C."""
    runner = web.AppRunner(app)

    async def serve():
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", port)
            await site.start()

            addresses = runner.addresses
            if not addresses:
                raise RuntimeError("Video server started without a bound address")
            actual_port = int(addresses[0][1])
            url = f"http://127.0.0.1:{actual_port}"
            click.echo(f"Video stream available at: {url}")
            click.echo(f"Snapshot endpoint: {url}/snapshot")
            click.echo("Press Ctrl+C to stop.")

            if open_browser:
                webbrowser.open(url)

            from anyio import sleep_forever
            await sleep_forever()
        finally:
            with move_on_after(2, shield=True):
                await runner.cleanup()

    try:
        client.portal.call(serve)
    except KeyboardInterrupt:
        click.echo("\nStopping video server.")


async def _proxy_mjpeg_stream(client, request):
    """Proxy ustreamer's native MJPEG stream through the jumpstarter tunnel."""
    async with client.stream_async("connect") as tunnel:
        await tunnel.send(b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")

        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += await tunnel.receive()

        header_part, _, body_start = buf.partition(b"\r\n\r\n")

        response = web.StreamResponse()
        response.content_type = _parse_content_type(header_part)
        await response.prepare(request)

        if body_start:
            await response.write(body_start)

        try:
            while True:
                chunk = await tunnel.receive()
                await response.write(chunk)
        except (EndOfStream, ConnectionResetError, ConnectionAbortedError, get_cancelled_exc_class()):
            pass

    return response


class UStreamerClient(DriverClient):
    """UStreamer client class

    Client methods for the UStreamer driver.
    """

    def state(self):
        """Get state of ustreamer service"""
        return UStreamerState.model_validate(self.call("state"))

    def snapshot(self):
        """Get a snapshot image from the video input

        :return: PIL Image object of the snapshot image
        :rtype: PIL.Image
        """
        input_jpg_data = b64decode(self.call("snapshot"))
        return Image.open(io.BytesIO(input_jpg_data))

    def snapshot_bytes(self):
        """Get raw JPEG bytes from the video input"""
        return b64decode(self.call("snapshot"))

    def cli(self):
        @driver_click_group(self)
        def video():
            """Video capture and streaming"""
            pass

        @video.command()
        def state():
            """Show video source state"""
            s = self.state()
            src = s.result.source
            enc = s.result.encoder
            click.echo(f"Online:     {src.online}")
            click.echo(f"Resolution: {src.resolution.width}x{src.resolution.height}")
            click.echo(f"FPS:        {src.captured_fps}/{src.desired_fps}")
            click.echo(f"Encoder:    {enc.type} (quality: {enc.quality})")

        @video.command()
        @click.option("-o", "--output", default="snapshot.jpg", help="Output file path")
        def snapshot(output):
            """Save a single snapshot to file"""
            img = self.snapshot()
            img.save(output)
            click.echo(f"Saved snapshot to {output}")

        @video.command()
        @click.option("-p", "--port", default=0, type=int, help="Local server port (0 = auto)")
        @click.option("--browser/--no-browser", default=True, help="Open in web browser")
        def stream(port, browser):
            """Start local MJPEG streaming server

            Proxies ustreamer's native MJPEG stream through the jumpstarter
            tunnel. Frame rate is controlled by ustreamer's configuration.
            """

            async def handle_index(request):
                return web.Response(text=LANDING_PAGE, content_type="text/html")

            async def handle_snapshot(request):
                data = b64decode(await self.call_async("snapshot"))
                return web.Response(body=data, content_type="image/jpeg")

            async def handle_stream(request):
                return await _proxy_mjpeg_stream(self, request)

            app = web.Application()
            app.router.add_get("/", handle_index)
            app.router.add_get("/snapshot", handle_snapshot)
            app.router.add_get("/stream", handle_stream)

            _run_server(self, app, port, browser)

        return video

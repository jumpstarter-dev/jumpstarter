import io
import shutil
import subprocess
import sys
import webbrowser
from base64 import b64decode
from pathlib import Path

import click
from aiohttp import web
from anyio import EndOfStream, get_cancelled_exc_class, move_on_after, sleep_forever
from PIL import Image

from .common import VideoState
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


def _is_chunked(header_bytes: bytes) -> bool:
    """Whether the response body uses HTTP chunked transfer encoding."""
    for line in header_bytes.decode("ascii", errors="replace").split("\r\n"):
        name, _, value = line.partition(":")
        if name.strip().lower() == "transfer-encoding" and "chunked" in value.lower():
            return True
    return False


async def _iter_body(tunnel, buf: bytes, chunked: bool):
    """Yield response body bytes from the tunnel, undoing chunked framing.

    Sources that serve MJPEG from an HTTP server which chunks its output (the
    ESP-IDF http server does) would otherwise leak chunk-size lines into the
    multipart body, corrupting the stream for the client.
    """
    if not chunked:
        if buf:
            yield buf
        while True:
            yield await tunnel.receive()

    while True:
        while b"\r\n" not in buf:
            buf += await tunnel.receive()
        size_line, _, buf = buf.partition(b"\r\n")
        try:
            size = int(size_line.split(b";")[0].strip(), 16)
        except ValueError:
            raise web.HTTPBadGateway(reason="invalid chunk size from upstream") from None
        if size == 0:
            return
        if size > _MAX_CHUNK_SIZE:
            raise web.HTTPBadGateway(reason="upstream chunk too large")
        while len(buf) < size + 2:
            buf += await tunnel.receive()
        yield buf[:size]
        buf = buf[size + 2 :]  # drop the CRLF terminating the chunk


# Candidate native players, best first. VLC leads because it survives the stream
# dropping (a lease ending, the camera re-enumerating) instead of exiting, and
# handles bare MJPEG-over-HTTP without a container. Each entry is
# (binary, extra argv) -- the URL is appended last.
#
# macOS installs VLC and IINA as .app bundles whose binaries are not on PATH, so
# those are probed separately.
_PLAYERS = {
    # --demux=mjpeg is required: without it VLC probes for a container format and
    # usually fails on a raw MJPEG stream.
    "vlc": ["--demux=mjpeg", "--network-caching=300"],
    "mpv": ["--demuxer-lavf-format=mjpeg", "--profile=low-latency", "--untimed"],
    # ffplay has no reconnect logic, so it is the last resort of the three.
    "ffplay": ["-fflags", "nobuffer", "-flags", "low_delay", "-loglevel", "warning"],
}

_MACOS_APP_BINARIES = (
    "/Applications/VLC.app/Contents/MacOS/VLC",
    "/Applications/IINA.app/Contents/MacOS/IINA",
    "/Applications/mpv.app/Contents/MacOS/mpv",
)


def find_player(preferred: str | None = None) -> tuple[str, list[str]] | None:
    """Locate a native video player.

    Returns ``(executable, extra_args)`` or ``None`` when nothing is installed.
    Works the same on Linux (players on PATH) and macOS (also .app bundles).
    """
    if preferred:
        path = shutil.which(preferred)
        if path:
            return path, _PLAYERS.get(Path(preferred).name, [])
        # Allow an explicit absolute path to a player binary.
        if Path(preferred).is_file():
            return preferred, _PLAYERS.get(Path(preferred).name, [])
        return None

    for name, args in _PLAYERS.items():
        path = shutil.which(name)
        if path:
            return path, args

    if sys.platform == "darwin":
        for path in _MACOS_APP_BINARIES:
            if Path(path).is_file():
                # Key the args off the binary name, lowercased: the VLC bundle's
                # binary is "VLC" while the flags are registered under "vlc".
                return path, _PLAYERS.get(Path(path).name.lower(), [])

    return None


def open_in_player(url: str, preferred: str | None = None) -> subprocess.Popen | None:
    """Launch a native player on ``url``. Returns the process, or None if none found."""
    found = find_player(preferred)
    if found is None:
        return None
    executable, extra = found
    click.echo(f"Opening in {Path(executable).name}: {url}")
    # stdout/stderr discarded: players are chatty on stderr about codec probing
    # and would bury the server's own output.
    return subprocess.Popen(
        [executable, *extra, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_video_server(client, app, port, open_browser, player=None):
    """Run an aiohttp app, opening a viewer and blocking until Ctrl+C.

    ``open_browser`` opens the landing page in a web browser. ``player`` opens the
    MJPEG stream directly in a native player instead -- pass ``"auto"`` to pick
    whichever of vlc/mpv/ffplay is installed, or a specific binary name/path.
    """
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

            proc = None
            if player:
                # The stream endpoint, not the landing page: a player wants the
                # MJPEG bytes, not HTML.
                proc = open_in_player(f"{url}/stream", None if player == "auto" else player)
                if proc is None:
                    click.echo(
                        "No native player found (tried vlc, mpv, ffplay). "
                        "Install one, or use --browser.",
                        err=True,
                    )
                    click.echo(f"The stream is still available at {url}/stream")
            elif open_browser:
                webbrowser.open(url)

            try:
                await sleep_forever()
            finally:
                # Close the player when the server stops, so Ctrl+C does not
                # leave an orphaned window showing a frozen frame.
                if proc is not None and proc.poll() is None:
                    proc.terminate()
        finally:
            with move_on_after(2, shield=True):
                await runner.cleanup()

    try:
        client.portal.call(serve)
    except KeyboardInterrupt:
        click.echo("\nStopping video server.")


def _parse_status_code(header_bytes: bytes) -> int:
    """Extract HTTP status code from the first line of raw response headers."""
    first_line = header_bytes.decode("ascii", errors="replace").split("\r\n", 1)[0]
    parts = first_line.split(None, 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


_MAX_HEADER_SIZE = 16 * 1024
_MAX_CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB — generous for MJPEG frames


async def proxy_mjpeg_stream(client, request, path):
    """Proxy the source's native MJPEG stream through the jumpstarter tunnel."""
    async with client.stream_async("connect") as tunnel:
        await tunnel.send(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))

        buf = b""
        try:
            while b"\r\n\r\n" not in buf:
                if len(buf) > _MAX_HEADER_SIZE:
                    raise web.HTTPBadGateway(reason="upstream response headers too large")
                buf += await tunnel.receive()
        except EndOfStream:
            raise web.HTTPBadGateway(reason="upstream closed before sending complete headers") from None

        header_part, _, body_start = buf.partition(b"\r\n\r\n")

        status = _parse_status_code(header_part)
        if status == 0:
            raise web.HTTPBadGateway(reason="invalid upstream status line")

        response = web.StreamResponse(status=status)
        response.content_type = _parse_content_type(header_part)
        await response.prepare(request)

        try:
            async for chunk in _iter_body(tunnel, body_start, _is_chunked(header_part)):
                await response.write(chunk)
        except (EndOfStream, ConnectionResetError, ConnectionAbortedError, get_cancelled_exc_class()):
            pass

    return response


class VideoClient(DriverClient):
    """Client for video source drivers implementing VideoInterface."""

    def snapshot(self):
        """Get a snapshot image from the video input

        :return: PIL Image object of the snapshot image
        :rtype: PIL.Image
        """
        return Image.open(io.BytesIO(self.snapshot_bytes()))

    def snapshot_bytes(self) -> bytes:
        """Get raw JPEG bytes from the video input"""
        return b64decode(self.call("snapshot"))

    def stream_path(self) -> str:
        """HTTP path serving the MJPEG stream on the ``connect`` tunnel"""
        return self.call("stream_path")

    def state(self) -> VideoState:
        """Get state of the video source

        :return: common video source state
        :rtype: VideoState
        """
        return VideoState.model_validate(self.call("state"))

    def cli(self):
        @driver_click_group(self)
        def video():
            """Video capture and streaming"""
            pass

        @video.command()
        def state():
            """Show video source state"""
            s = self.state()
            click.echo(f"Online:     {s.online}")
            if s.width is not None and s.height is not None:
                click.echo(f"Resolution: {s.width}x{s.height}")
            if s.fps is not None:
                click.echo(f"FPS:        {s.fps}")

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
        @click.option(
            "--player",
            is_flag=False,
            flag_value="auto",
            default=None,
            help="Open in a native player instead of a browser. "
            "Bare --player picks vlc/mpv/ffplay automatically; "
            "--player=vlc names one (works on macOS .app bundles too).",
        )
        def stream(port, browser, player):
            """Start local MJPEG streaming server

            Proxies the source's native MJPEG stream through the jumpstarter
            tunnel. Frame rate is controlled by the video source.

            By default the landing page opens in a web browser. Pass --player to
            open the raw stream in VLC, mpv, or ffplay instead, which is usually
            smoother for watching a bench for a long time.
            """
            path = self.stream_path()

            async def handle_index(request):
                return web.Response(text=LANDING_PAGE, content_type="text/html")

            async def handle_snapshot(request):
                data = b64decode(await self.call_async("snapshot"))
                return web.Response(body=data, content_type="image/jpeg")

            async def handle_stream(request):
                return await proxy_mjpeg_stream(self, request, path)

            app = web.Application()
            app.router.add_get("/", handle_index)
            app.router.add_get("/snapshot", handle_snapshot)
            app.router.add_get("/stream", handle_stream)

            run_video_server(self, app, port, browser, player)

        return video

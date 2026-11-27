"""The local web view of an HMI (``j <device> hmi view``): a page served from this machine, and a WebSocket that relays
one HMI to it.

::

    serve_view(client)          # blocks until Ctrl-C; prints http://127.0.0.1:<port>/ and opens the browser

The server runs on this machine only: it binds a loopback address (never anything else: ``serve_view`` refuses any
other ``host``), and it refuses a request whose ``Host`` is not a loopback name with this server's port (a web page
on another site cannot reach it through a name that resolves here) and, for the WebSocket, one whose ``Origin`` is
not the page's own (a web page on another site cannot drive the HMI from the user's browser).

Nor can another process on this machine use it: every session has a secret token (``secrets.token_urlsafe(16)``, or
the ``token`` given to :func:`create_app`), and ``/info`` and ``/ws`` answer only a request that carries it as
``?token=...`` (403 otherwise). :func:`serve_view` prints and opens the address with the token in it, and that printed
address is the only place the token is written: the access log is off, because it would write it with every request.

**Routes**

``/`` and ``/hmi.js``
    The page (package data: no build step, no CDN, it works offline). They are open: the token is in the page's address,
    and the page passes it on.
``/info?token=...``
    ``client.hmi_info()`` as JSON: what the page needs to offer only the input the HMI declares (an HMI that declares
    none is shown, and nothing is sent to it).
``/ws?token=...``
    The relay, one to one with the HMI's own video frames. Server to browser: a TEXT frame is the codec line, unchanged
    (it names the codec in use: ``avc1.*``, ``hev1.*``/``hvc1.*`` or ``mjpeg``), or an ``{"error": "..."}`` the exporter
    answered an input with; a BINARY frame is the 8-byte big-endian microsecond timestamp followed by one access unit
    (Annex-B for H.264 and HEVC, one JPEG for MJPEG), unchanged. Browser to server, TEXT frames of JSON objects:
    ``{"resync": true}``, ``{"lag": seconds}`` and the input events of the interface, ``{"pointer": {...}}``,
    ``{"touch": {...}}``, ``{"keyboard": {...}}``, ``{"key": {...}}``, ``{"rotary": {...}}`` or ``{"text": "..."}`` (see
    :mod:`jumpstarter_driver_hmi.common` for their fields: the exporter checks them against what the HMI declares and
    answers what it cannot take). A message over ``MAX_LINE_BYTES``, the interface's limit for an input line, closes the
    socket (1009). Anything else is ignored. The socket closes with 1000 when the stream ends, 1011 (and a
    reason) when it could not be opened or ended with an error, and 4403 when the exporter refused it.

The client is blocking and its calls run on the Jumpstarter portal's thread, so the event loop here never calls it:
every blocking call runs on a thread (one of anyio's pool, or one of the relay's own), and a thread of the relay's
own hands what it has to the loop through the connection's ``anyio.from_thread.BlockingPortal`` (see :class:`_Relay`
for why not ``anyio.from_thread.run``). The video reader waits for each send to the browser to finish, so a slow
browser backs up the socket, then the reader, then the exporter's own queue for this viewer (which sheds pictures:
the designed behavior), and nothing in between grows."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import math
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager, suppress
from importlib import resources
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import anyio
import anyio.from_thread
import click
from aiohttp import WSCloseCode, WSMsgType, web
from anyio import to_thread

from ..client import REFUSED, HmiClient, HmiStreamClosedError
from ..common import MAX_LINE_BYTES

if TYPE_CHECKING:
    from ..client import InputStream, VideoStream

LOGGER = logging.getLogger(__name__)

#: The largest message the browser may send: the interface's own limit for an input line.
MAX_MESSAGE_BYTES = MAX_LINE_BYTES
#: How often a worker thread looks at whether the connection has ended while it waits for the stream.
READ_POLL_S = 0.25
#: How long the stream may take to say what it carries (the exporter may be starting the device's side).
OPEN_TIMEOUT_S = 60.0
#: How long a connection's end waits for its streams to close before it lets them go (a thread ends them by itself).
TEARDOWN_S = 5.0
#: How long the end of the server waits for the sockets to close.
SHUTDOWN_S = 3.0
HEARTBEAT_S = 20.0
#: After the input stream failed to open, the next events are refused without trying again for this long.
INPUT_RETRY_S = 2.0
#: The close code of a stream the exporter refused (a policy or a permission: the WebSocket's 403).
CLOSE_REFUSED = 4403
#: What the exporter may be given as a lag: seconds.
MAX_LAG_S = 3600.0
#: The input events the browser may send, and the type of each one's body. The exporter checks what is inside.
INPUT_EVENTS: tuple[tuple[str, type], ...] = (
    ("pointer", dict),
    ("touch", dict),
    ("keyboard", dict),
    ("key", dict),
    ("rotary", dict),
    ("text", str),
)

#: Host names (with the server's port) the server answers to. Anything else is a request made through some other name.
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
#: Paths that show or drive the HMI: they want the session's token, and their ``Origin`` is checked (a cross-site
#: page must not read the capabilities either). The page and its script give nothing away, so they are open.
_PROTECTED = frozenset({"/ws", "/info"})


# -- the address -----------------------------------------------------------------------------------------------------


def bind_address(host: str) -> str:
    """The address to bind for ``host``: ``127.0.0.1`` for ``localhost``, or the loopback address given (``::1``...).

    Raises:
        ValueError: If ``host`` is anything else: the view never listens beyond this machine.
    """
    name = host.strip().lower().strip("[]") if isinstance(host, str) else ""
    if name == "localhost":
        return "127.0.0.1"
    refusal = ValueError(f"the HMI view listens on a loopback address only (127.0.0.1, localhost, ::1), not {host!r}")
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        raise refusal from None
    if not address.is_loopback:
        raise refusal
    return str(address)


def view_url(address: str, port: int, token: str | None = None) -> str:
    """The URL of the page for a bound ``address`` and ``port``, with the session's ``token`` in it if there is one."""
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}:{port}/" + (f"?token={quote(token, safe='')}" if token else "")


def _host_allowed(header: str | None, port: int | None) -> bool:
    """Whether a ``Host`` header names this machine (a loopback name) and this server's port."""
    if not header or port is None:
        return False
    if header.startswith("["):
        end = header.find("]")
        if end < 0:
            return False
        name, rest = header[: end + 1], header[end + 1 :]
        if rest and not rest.startswith(":"):
            return False
        port_text = rest[1:]
    else:
        name, _, port_text = header.partition(":")
    if name.lower() not in _LOOPBACK_NAMES:
        return False
    if not port_text:
        return port == 80
    return port_text.isascii() and port_text.isdigit() and int(port_text) == port


def _origin_allowed(origin: str | None, host: str) -> bool:
    """Whether an ``Origin`` is absent (not a browser) or is this page's own."""
    return origin is None or origin.lower() == f"http://{host}".lower()


def _token_allowed(given: str | None, expected: str) -> bool:
    """Whether the ``token`` of a request is the session's (compared without giving away how much of it was right)."""
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


@web.middleware
async def _guard(request: web.Request, handler: Callable) -> web.StreamResponse:
    """Refuse (403) a request through a name that is not this machine's, and for ``/info`` and ``/ws`` one from another
    site or without the session's token."""
    sockname = request.transport.get_extra_info("sockname") if request.transport is not None else None
    host = request.headers.get("Host")
    if not _host_allowed(host, sockname[1] if sockname else None):
        raise web.HTTPForbidden(text="this server answers to 127.0.0.1 and localhost only\n")
    if request.path in _PROTECTED:
        if not _origin_allowed(request.headers.get("Origin"), host or ""):
            raise web.HTTPForbidden(text="cross-origin requests are refused\n")
        if not _token_allowed(request.query.get("token"), request.app[TOKEN]):
            raise web.HTTPForbidden(text="this needs the address that j <device> hmi view printed, with its token\n")
    return await handler(request)


async def _plain_headers(request: web.Request, response: web.StreamResponse) -> None:
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")


# -- the pages -------------------------------------------------------------------------------------------------------


def _asset(name: str) -> bytes:
    return (resources.files(__name__.rpartition(".")[0]) / "static" / name).read_bytes()


async def _index(request: web.Request) -> web.Response:
    host = request.headers["Host"]
    policy = (
        "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; "
        f"connect-src 'self' ws://{host}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return web.Response(
        body=request.app[ASSETS]["index.html"],
        content_type="text/html",
        charset="utf-8",
        headers={"Content-Security-Policy": policy, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"},
    )


async def _script(request: web.Request) -> web.Response:
    return web.Response(body=request.app[ASSETS]["hmi.js"], content_type="text/javascript", charset="utf-8")


async def _info(request: web.Request) -> web.Response:
    client = request.app[CLIENT]
    try:
        info = await to_thread.run_sync(client.hmi_info)
    except Exception as e:  # noqa: BLE001 (whatever the exporter says is for the page to show)
        return web.json_response({"error": str(e) or type(e).__name__}, status=502)
    return web.json_response(info)


# -- the relay -------------------------------------------------------------------------------------------------------


class Workers:
    """Counts the threads the relays have running: the proof, for a test and for a clean shutdown, that none is left."""

    def __init__(self) -> None:
        """Nothing running."""
        self._cv = threading.Condition()
        self._count = 0

    def _add(self) -> None:
        with self._cv:
            self._count += 1

    def _done(self) -> None:
        with self._cv:
            self._count -= 1
            self._cv.notify_all()

    @contextmanager
    def track(self) -> Generator[None]:
        """One thread's life (a call a coroutine hands to anyio's pool, which may outlive the coroutine)."""
        self._add()
        try:
            yield
        finally:
            self._done()

    def spawn(self, name: str, target: Callable[..., None], *args: Any) -> threading.Thread:
        """Start a daemon thread of its own that runs ``target(*args)``; it counts from now, not from when it runs."""

        def run() -> None:
            try:
                target(*args)
            finally:
                self._done()

        self._add()
        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return thread

    @property
    def count(self) -> int:
        """How many threads are running."""
        with self._cv:
            return self._count

    def wait_idle(self, timeout: float) -> bool:
        """Wait until no thread is running; False if some still are after ``timeout`` seconds."""
        with self._cv:
            return self._cv.wait_for(lambda: self._count == 0, timeout)


CLIENT = web.AppKey("hmi_client", HmiClient)
WORKERS = web.AppKey("hmi_workers", Workers)
SOCKETS = web.AppKey("hmi_sockets", set)
ASSETS = web.AppKey("hmi_assets", dict)
#: The session's secret (``app[TOKEN]``): what ``/info`` and ``/ws`` want as ``?token=``.
TOKEN = web.AppKey("hmi_token", str)


class _Gone(Exception):
    """The browser's socket is gone: the thread that was sending to it ends."""


def _short(text: str) -> bytes:
    """A close reason: a WebSocket close frame carries at most 123 bytes."""
    return text.encode()[:120].decode(errors="ignore").encode()


def _closing(error: HmiStreamClosedError) -> tuple[int, bytes]:
    if error.reason == REFUSED:
        return CLOSE_REFUSED, b"refused"
    return WSCloseCode.INTERNAL_ERROR, _short(error.detail or str(error))


def _stream_end(video: VideoStream) -> tuple[int, bytes]:
    """How a stream that ended closes the socket: 4403 if refused, 1011 if it broke, 1000 if it ended."""
    if video.end_reason == REFUSED:
        return CLOSE_REFUSED, b"refused"
    if video.end_reason == "error":
        return WSCloseCode.INTERNAL_ERROR, _short(str(video.end_error or "the video stream broke"))
    return WSCloseCode.OK, b"the stream ended"


class _Relay:
    """One browser's WebSocket and the streams of the HMI behind it.

    Two kinds of thread work for it, and they differ in what they may do. A **thread of its own**
    (``Workers.spawn``: the video reader, the reader of the input errors) hands what it has to the loop through this
    connection's ``BlockingPortal`` (``anyio.from_thread``'s own tool for a thread that is not one of anyio's pool):
    when the connection ends the portal stops and such a thread's call raises, so it ends. A call that a coroutine
    hands to **anyio's pool** (one input event, one request to the video stream) never calls back into the loop, so
    the coroutine may be cancelled and leave it running. (A pool thread that did call back would run its call inside
    the cancelled scope it was abandoned from, where anyio's checkpoints spin: that is why the two are kept apart.)
    """

    def __init__(self, client: HmiClient, ws: web.WebSocketResponse, workers: Workers) -> None:
        """A relay for ``ws`` (nothing is opened until :meth:`run`)."""
        self.client = client
        self.ws = ws
        self.workers = workers
        #: Set when the connection is over: every thread looks at it between its blocking calls and ends.
        self.stop = threading.Event()
        #: The open video stream (set by its thread, for the browser's resync and lag).
        self.video: VideoStream | None = None
        self._send_lock = anyio.Lock()
        self._portal: anyio.from_thread.BlockingPortal | None = None
        self._input_lock = threading.Lock()
        self._input_stack: ExitStack | None = None
        self._input_stream: InputStream | None = None
        self._input_failed: tuple[float, HmiStreamClosedError] | None = None

    async def run(self) -> None:
        """Relay until the browser leaves or the stream ends, then close everything."""
        try:
            async with anyio.from_thread.BlockingPortal() as portal:
                self._portal = portal
                self.workers.spawn("hmi-view-video", self._video_worker)
                try:
                    await self._receive()
                finally:
                    self.stop.set()
                    with anyio.CancelScope(shield=True):
                        # a thread still waiting for the loop is let go: its call raises
                        await portal.stop(cancel_remaining=True)
        except Exception:
            LOGGER.warning("HMI view: the relay failed", exc_info=True)
        finally:
            await self._teardown()

    # -- video: stream to socket --------------------------------------------------------------------------------------

    def _video_worker(self) -> None:
        """On a thread of its own: open the stream, pass its frames on one by one, and when it is over close the socket
        with the code that says how it ended."""
        result = self._relay_video()
        if result is not None and not self.stop.is_set():
            with suppress(Exception):
                self._call(self._close_socket, *result)

    async def _close_socket(self, code: int, reason: bytes) -> None:
        with anyio.move_on_after(TEARDOWN_S, shield=True), suppress(Exception):
            await self.ws.close(code=code, message=reason)

    def _call(self, function: Callable[..., Any], *args: Any) -> None:
        """From a thread of its own: run ``function`` on the loop and wait for it."""
        portal = self._portal
        if portal is None:
            raise _Gone
        portal.call(function, *args)

    def _relay_video(self) -> tuple[int, bytes] | None:
        """``(close code, reason)`` for how the stream ended, or None if the socket is gone."""
        try:
            with self.client.video(timeout=OPEN_TIMEOUT_S) as video:
                if self.stop.is_set():
                    return None
                self.video = video
                try:
                    return self._pass_frames(video)
                finally:
                    self.video = None
        except _Gone:
            return None
        except HmiStreamClosedError as e:
            return _closing(e)
        except TimeoutError:
            return WSCloseCode.INTERNAL_ERROR, b"the HMI did not start in time"
        except Exception:
            LOGGER.warning("HMI view: the video stream broke", exc_info=True)
            return WSCloseCode.INTERNAL_ERROR, b"the video stream broke"

    def _pass_frames(self, video: VideoStream) -> tuple[int, bytes] | None:
        while not self.stop.is_set():
            try:
                frame = video.read_frame(READ_POLL_S)
            except TimeoutError:
                continue
            if frame is None:
                return _stream_end(video)
            self._deliver(frame)
        return None

    def _deliver(self, frame: str | bytes) -> None:
        """Send one frame and wait until the socket has taken it: a slow browser slows this reader, and so the
        exporter's queue for this viewer sheds pictures."""
        try:
            self._call(self._send_text if isinstance(frame, str) else self._send_bytes, frame)
        except _Gone:
            raise
        except Exception as e:
            raise _Gone from e

    async def _send_text(self, text: str) -> None:
        async with self._send_lock:
            await self.ws.send_str(text)

    async def _send_bytes(self, data: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(data)

    async def _send_error(self, message: str) -> None:
        with suppress(Exception):
            await self._send_text(json.dumps({"error": message}))

    # -- browser to server --------------------------------------------------------------------------------------------

    async def _receive(self) -> None:
        """The browser's messages, until it closes the socket. Binary ones and what is not understood are ignored."""
        async for message in self.ws:
            if message.type is WSMsgType.TEXT:
                await self._on_text(message.data)
            elif message.type is WSMsgType.ERROR:
                break

    async def _on_text(self, text: str) -> None:
        try:
            event = json.loads(text)
        except (ValueError, RecursionError):
            return
        if not isinstance(event, dict):
            return
        if event.get("resync") is True:
            await self._on_video("request_resync")
        lag = event.get("lag")
        if isinstance(lag, int | float) and not isinstance(lag, bool) and math.isfinite(lag) and 0 <= lag < MAX_LAG_S:
            await self._on_video("report_lag", float(lag))
        for name, kind in INPUT_EVENTS:
            value = event.get(name)
            if isinstance(value, kind):
                await self._on_input({name: value})

    async def _on_video(self, method: str, *args: Any) -> None:
        """A request for the video stream (on a pool thread: the client blocks). Lost when the stream is not open."""
        video = self.video
        if video is not None:
            with suppress(Exception):
                await to_thread.run_sync(getattr(video, method), *args, abandon_on_cancel=True)

    async def _on_input(self, event: dict) -> None:
        """One input event to the HMI, opening the input stream on the first one. What cannot be done is answered to the
        browser."""
        try:
            stream = await self._input()
            try:
                await to_thread.run_sync(stream.send, event, abandon_on_cancel=True)
            except HmiStreamClosedError:
                # the stream has ended: let go of it, so that the next event opens it again
                await to_thread.run_sync(self._drop_input, stream, abandon_on_cancel=True)
                raise
        except HmiStreamClosedError as e:
            await self._send_error(e.detail or str(e))
        except Exception as e:
            LOGGER.warning("HMI view: an input event failed", exc_info=True)
            await self._send_error(str(e) or "the input could not be sent")

    async def _input(self) -> InputStream:
        """The input stream, opened now if this is the first event (or the last one's stream ended)."""
        stream = self._input_stream
        if stream is not None:
            return stream
        failed = self._input_failed
        if failed is not None and time.monotonic() - failed[0] < INPUT_RETRY_S:
            raise failed[1]
        try:
            stream = await to_thread.run_sync(self._open_input, abandon_on_cancel=True)
        except HmiStreamClosedError as e:
            self._input_failed = (time.monotonic(), e)
            raise
        self._input_failed = None
        self.workers.spawn("hmi-view-errors", self._errors_worker, stream)
        return stream

    def _open_input(self) -> InputStream:
        """On a pool thread. A connection that ended while this was opening closes what it opened: nothing is left."""
        with self.workers.track(), self._input_lock:
            if self._input_stream is not None:
                return self._input_stream
            if self.stop.is_set():
                raise HmiStreamClosedError("the view has closed", reason="ended")
            stack = ExitStack()
            try:
                stream = stack.enter_context(self.client.input())
                if self.stop.is_set():
                    raise HmiStreamClosedError("the view has closed", reason="ended")
            except BaseException:
                stack.close()
                raise
            self._input_stack, self._input_stream = stack, stream
            return stream

    def _drop_input(self, stream: InputStream) -> None:
        with self.workers.track():
            with self._input_lock:
                if self._input_stream is not stream:
                    return
                stack, self._input_stack, self._input_stream = self._input_stack, None, None
            if stack is not None:
                with suppress(Exception):
                    stack.close()

    def _close_input(self) -> None:
        """On a pool thread: close the input stream (the exporter lifts a finger that is down and lets go of keys)."""
        with self.workers.track():
            with self._input_lock:
                stack, self._input_stack, self._input_stream = self._input_stack, None, None
            if stack is not None:
                with suppress(Exception):
                    stack.close()

    def _errors_worker(self, stream: InputStream) -> None:
        """On a thread of its own: what the exporter answers an input with (``{"error": ...}``) goes to the browser."""
        while not self.stop.is_set():
            try:
                message = stream.read_error(READ_POLL_S)
            except TimeoutError:
                continue
            except Exception:  # noqa: BLE001 (the stream broke: it is over)
                return
            if message is None:
                return
            try:
                self._call(self._send_error, message)
            except Exception:  # noqa: BLE001
                return

    # -- the end ------------------------------------------------------------------------------------------------------

    async def _teardown(self) -> None:
        """The connection is over: tell the threads, close the input stream (this lifts the finger) and the socket.

        Bounded in time: what does not finish within ``TEARDOWN_S`` seconds is left to finish by itself."""
        self.stop.set()
        with anyio.move_on_after(TEARDOWN_S, shield=True):
            await to_thread.run_sync(self._close_input, abandon_on_cancel=True)
            if not self.ws.closed:
                with suppress(Exception):
                    await self.ws.close(code=WSCloseCode.GOING_AWAY, message=b"the view is closing")


async def _ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES, heartbeat=HEARTBEAT_S, compress=False)
    await ws.prepare(request)
    sockets: set = request.app[SOCKETS]
    sockets.add(ws)
    try:
        await _Relay(request.app[CLIENT], ws, request.app[WORKERS]).run()
    finally:
        sockets.discard(ws)
    return ws


async def _close_sockets(app: web.Application) -> None:
    """The server is closing: end every relay now (its page reconnects, or finds the server gone)."""

    async def close(ws: web.WebSocketResponse) -> None:
        with suppress(Exception):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"the view is closing")

    with anyio.move_on_after(SHUTDOWN_S, shield=True):
        async with anyio.create_task_group() as group:
            for ws in list(app[SOCKETS]):
                group.start_soon(close, ws)


# -- the server ------------------------------------------------------------------------------------------------------


def create_app(client: HmiClient, *, token: str | None = None) -> web.Application:
    """The web application for ``client``'s HMI (see the module documentation for its routes).

    It answers only a request that names this machine (``127.0.0.1``, ``localhost`` or ``[::1]``, with the port it is
    listening on), and ``/info`` and ``/ws`` only one that carries the session's secret as ``?token=``. That is
    ``token``, or a new ``secrets.token_urlsafe(16)`` if none is given; either way it is ``app[TOKEN]``.

    Raises:
        ValueError: If ``token`` is given and is empty.
    """
    if token is not None and not token:
        raise ValueError("the token must not be empty")
    app = web.Application(middlewares=[_guard])
    app[TOKEN] = token if token is not None else secrets.token_urlsafe(16)
    app[CLIENT] = client
    app[WORKERS] = Workers()
    app[SOCKETS] = set()
    app[ASSETS] = {"index.html": _asset("index.html"), "hmi.js": _asset("hmi.js")}
    app.on_response_prepare.append(_plain_headers)
    app.on_shutdown.append(_close_sockets)
    app.router.add_get("/", _index)
    app.router.add_get("/hmi.js", _script)
    app.router.add_get("/info", _info)
    app.router.add_get("/ws", _ws)
    return app


async def run_server(
    client: HmiClient, host: str = "127.0.0.1", port: int = 0, *, token: str | None = None
) -> tuple[web.AppRunner, int]:
    """Start the server on a loopback address; ``(runner, port)``, the port being the one it listens on (0: a free one).

    The session's token (see :func:`create_app`) is ``runner.app[TOKEN]``. The caller ends the server with
    ``await runner.cleanup()``. There is no access log: it would write the token of every request.

    Raises:
        ValueError: If ``host`` is not a loopback address, or ``token`` is empty.
    """
    address = bind_address(host)
    runner = web.AppRunner(create_app(client, token=token), shutdown_timeout=SHUTDOWN_S, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, address, port).start()
    except BaseException:
        await runner.cleanup()
        raise
    return runner, runner.addresses[0][1]


async def _serve(client: HmiClient, address: str, port: int, open_browser: bool) -> None:
    runner, bound = await run_server(client, address, port)
    url = view_url(address, bound, runner.app[TOKEN])
    try:
        click.echo(f"HMI view: {url}  (Ctrl-C to stop)")
        if open_browser:
            with suppress(Exception):
                await to_thread.run_sync(webbrowser.open, url, abandon_on_cancel=True)
        await anyio.sleep_forever()
    finally:
        with anyio.move_on_after(SHUTDOWN_S + 2, shield=True):
            await runner.cleanup()


def serve_view(client: HmiClient, *, port: int = 0, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    """Serve the page for ``client``'s HMI and block until Ctrl-C: prints the URL, with this session's token in it,
    and opens the browser at it (unless ``open_browser`` is False). The printed URL is the only place the token is
    written.

    Raises:
        ValueError: If ``host`` is not a loopback address (127.0.0.1, localhost, ::1): the view never listens beyond
            this machine.
        click.ClickException: If the port cannot be listened on.
    """
    address = bind_address(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError(f"the port must be from 0 to 65535: {port!r}")
    try:
        anyio.run(_serve, client, address, port, open_browser)
    except KeyboardInterrupt:
        click.echo("\nStopping the HMI view.")
    except OSError as e:
        raise click.ClickException(f"cannot listen on {address}:{port}: {e.strerror or e}") from e

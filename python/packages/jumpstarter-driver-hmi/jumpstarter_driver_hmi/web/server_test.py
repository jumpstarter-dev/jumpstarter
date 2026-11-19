"""The local web view: the server (routes, the guard, the relay and its ends) and the page's pure functions under node.

The screen behind the relay is a ``MockHmi`` through a real session (the real client, framing and hub), or a stub
client where a test needs the stream to do something a mock does not (be refused, block, be a fast source, say which
thread called it).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlsplit

import anyio
import pytest
from aiohttp import ClientConnectorError, ClientSession, WSMsgType, WSServerHandshakeError, web
from anyio import to_thread

from ..client import HmiClient, HmiStreamClosedError
from ..common import CodecLine
from ..driver import MockHmi
from ..hub.codecs import NAL_IDR, NAL_PPS, NAL_SPS, nal_types
from ..testing.fake import PPS, START_CODE, FakeSource, make_sps
from . import create_app, serve_view
from .server import SOCKETS, TOKEN, WORKERS, Workers, _serve, bind_address, run_server, view_url
from jumpstarter.common.utils import serve

pytestmark = pytest.mark.anyio

DEADLINE_S = 15.0
STATIC = Path(__file__).parent / "static"


# -- helpers -----------------------------------------------------------------------------------------------------------


async def wait_until(predicate: Callable[[], Any], what: str, timeout: float = DEADLINE_S) -> Any:
    """Wait until ``predicate()`` (a function, or a coroutine function) is truthy; fail after ``timeout`` seconds."""
    with anyio.move_on_after(timeout) as scope:
        while True:
            value = predicate()
            if inspect.isawaitable(value):
                value = await value
            if value:
                return value
            await anyio.sleep(0.01)
    assert not scope.cancelled_caught
    pytest.fail(f"timed out waiting for {what}")  # ty: ignore[invalid-argument-type]


async def receive(ws: Any, timeout: float = DEADLINE_S) -> Any:
    """The next message of a client WebSocket, or fail after ``timeout`` seconds."""
    with anyio.fail_after(timeout):
        return await ws.receive()


async def receive_frames(ws: Any, count: int) -> list[bytes]:
    """The next ``count`` binary frames (text frames in between are skipped)."""
    frames: list[bytes] = []
    while len(frames) < count:
        message = await receive(ws)
        assert message.type in (WSMsgType.TEXT, WSMsgType.BINARY), message
        if message.type is WSMsgType.BINARY:
            frames.append(message.data)
    return frames


async def receive_media(ws: Any, count: int) -> tuple[str, list[bytes]]:
    """The codec line, and then ``count`` binary frames, in order (the first message must be the codec line)."""
    first = await receive(ws)
    assert first.type is WSMsgType.TEXT, first
    return first.data, await receive_frames(ws, count)


async def receive_error(ws: Any) -> str:
    """The next ``{"error": ...}`` text frame (pictures in between are skipped)."""
    while True:
        message = await receive(ws)
        assert message.type in (WSMsgType.TEXT, WSMsgType.BINARY), message
        if message.type is WSMsgType.TEXT:
            return json.loads(message.data)["error"]


@dataclass
class Running:
    """A server on a free loopback port, in front of a screen."""

    runner: web.AppRunner
    port: int
    driver: MockHmi
    client: HmiClient

    @property
    def workers(self) -> Workers:
        return self.runner.app[WORKERS]

    @property
    def sockets(self) -> set:
        return self.runner.app[SOCKETS]

    def url(self, path: str = "/") -> str:
        """The address of ``path`` for this session (``/info`` and ``/ws`` with its token)."""
        return url_of(self.runner, self.port, path)

    @property
    def token(self) -> str:
        return self.runner.app[TOKEN]

    def received(self) -> list[tuple]:
        """What the device behind the screen has been given."""
        return fake_source(self.driver).sink_input.snapshot()

    async def viewers(self) -> list:
        return (await to_thread.run_sync(self.client.status))["viewers"]

    async def settled(self) -> None:
        """Every connection has ended and no worker thread is left (the deadline is the proof there is no hang)."""
        await wait_until(lambda: not self.sockets, "the connections to be over")
        assert await to_thread.run_sync(self.workers.wait_idle, DEADLINE_S), (
            f"{self.workers.count} worker threads are left"
        )
        await to_thread.run_sync(no_relay_threads)


def no_relay_threads() -> None:
    """The relay's own threads are all gone (each is joined, with a deadline: a thread that does not end is a leak)."""
    for thread in threading.enumerate():
        if thread.name.startswith("hmi-view-"):
            thread.join(DEADLINE_S)
            assert not thread.is_alive(), f"the thread {thread.name} is still running"


def url_of(runner: web.AppRunner, port: int, path: str = "/", *, host: str = "127.0.0.1") -> str:
    """The address of ``path`` on a server of ``run_server``: the paths that want the session's token have it."""
    query = f"?token={runner.app[TOKEN]}" if path in ("/ws", "/info") else ""
    return f"http://{host}:{port}{path}{query}"


def fake_source(driver: MockHmi) -> FakeSource:
    """The mock's source (made now if it has not been yet)."""
    return cast("FakeSource", driver._the_source())


def as_client(stub: object) -> HmiClient:
    """A stub where a client is wanted (the server only calls what the stub has)."""
    return cast("HmiClient", stub)


@contextmanager
def a_hmi(*, control: bool = True) -> Generator[tuple[MockHmi, HmiClient]]:
    driver = MockHmi(width=480, height=800, fps=30, gop=10, control=control)
    with serve(driver) as client:
        assert isinstance(client, HmiClient)
        yield driver, client


@pytest.fixture
def hmi() -> Iterator[tuple[MockHmi, HmiClient]]:
    with a_hmi() as pair:
        yield pair


@pytest.fixture
async def running(hmi: tuple[MockHmi, HmiClient]) -> AsyncIterator[Running]:
    driver, client = hmi
    runner, port = await run_server(client, "127.0.0.1", 0)
    try:
        yield Running(runner, port, driver, client)
    finally:
        await runner.cleanup()


TOUCH = {"type": "down", "x": 10, "y": 20, "id": 0, "w": 480, "h": 800}


# -- the pages ---------------------------------------------------------------------------------------------------------


async def test_the_page_and_its_script_are_served(running: Running) -> None:
    async with ClientSession() as session:
        async with session.get(running.url("/")) as response:
            assert response.status == 200
            assert response.content_type == "text/html"
            assert response.charset == "utf-8"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert f"ws://127.0.0.1:{running.port}" in response.headers["Content-Security-Policy"]
            body = await response.read()
            assert body == (STATIC / "index.html").read_bytes()
            assert b"<canvas" in body
        async with session.get(running.url("/hmi.js")) as response:
            assert response.status == 200
            assert response.content_type == "text/javascript"
            assert response.headers["Cache-Control"] == "no-store"
            body = await response.read()
            assert body == (STATIC / "hmi.js").read_bytes()
            assert b"VideoDecoder" in body
        async with session.get(running.url("/nothing")) as response:
            assert response.status == 404


def test_the_page_works_offline() -> None:
    page = (STATIC / "index.html").read_text()
    script = (STATIC / "hmi.js").read_text()
    for text in (page, script):
        assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", text), "an external address"
        assert "//cdn" not in text
    assert re.findall(r"<link[^>]*>", page) == ['<link rel="icon" href="data:,">']  # no request for a favicon either
    assert "@import" not in page
    assert re.findall(r"<script[^>]*>", page) == ['<script type="module" src="/hmi.js">']
    assert "import(" not in script
    assert not re.search(r"^import ", script, re.MULTILINE)


async def test_info_is_the_screens_info(running: Running) -> None:
    await to_thread.run_sync(running.client.start)
    await wait_until(lambda: running.client.hmi_info()["width"], "the picture's size")
    expected = await to_thread.run_sync(running.client.hmi_info)
    async with ClientSession() as session, session.get(running.url("/info")) as response:
        assert response.status == 200
        assert response.content_type == "application/json"
        assert await response.json() == expected
    assert expected["input"]["touch"] is True
    assert expected["input"]["hard_keys"]


# -- the relay ---------------------------------------------------------------------------------------------------------


async def test_the_socket_relays_the_codec_line_and_the_pictures(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        line, frames = await receive_media(ws, 12)
        assert json.loads(line)["codec"].startswith("avc1.")
        assert CodecLine.from_json(json.loads(line)).shown_size == (480, 800)
        stamps = [int.from_bytes(frame[:8], "big") for frame in frames]
        assert stamps == sorted(stamps)
        assert stamps[-1] > stamps[0]
        assert nal_types(frames[0][8:])[:3] == [NAL_SPS, NAL_PPS, NAL_IDR]
        assert all(nal_types(frame[8:]) for frame in frames)
    await running.settled()


async def test_touch_key_and_text_reach_the_device(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 1)
        await ws.send_json({"touch": TOUCH})
        await ws.send_json({"touch": {**TOUCH, "type": "up"}})
        await ws.send_json({"key": {"key": "HOME", "press": "short"}})
        await ws.send_json({"text": "hello"})
        wanted = [
            ("touch", "down", 10, 20, (480, 800)),
            ("touch", "up", 10, 20, (480, 800)),
            ("key", "HOME", "short"),
            ("text", "hello"),
        ]
        await wait_until(
            lambda: running.received()[: len(wanted)] == wanted, "the device to be given the events, in order"
        )
    await running.settled()


async def test_a_touch_of_the_wrong_size_is_answered_with_an_error(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 1)
        await ws.send_json({"touch": {**TOUCH, "w": 10, "h": 10}})
        assert "480x800" in await receive_error(ws)
        await ws.send_json({"key": {"key": "NO_SUCH_KEY", "press": "short"}})
        assert "NO_SUCH_KEY" in await receive_error(ws)
        assert await receive_frames(ws, 3)
    assert not any(event[0] == "touch" for event in running.received())
    await running.settled()


async def test_resync_and_lag_leave_the_stream_running(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 3)
        await ws.send_json({"resync": True})
        await ws.send_json({"lag": 0.1})
        await ws.send_json({"lag": 1.5})
        await receive_frames(ws, 10)
        key_after_resync = False
        for _ in range(100):
            message = await receive(ws)
            if message.type is WSMsgType.BINARY and NAL_IDR in nal_types(message.data[8:]):
                key_after_resync = True
                break
        assert key_after_resync
        await ws.send_json({"key": {"key": "BACK", "press": "short"}})
        await wait_until(lambda: ("key", "BACK", "short") in running.received(), "the key to arrive after a resync")
    await running.settled()


async def test_what_is_not_understood_is_ignored(running: Running) -> None:
    junk = [
        "not json",
        "{",
        "null",
        "[1, 2]",
        '"text"',
        "42",
        "{}",
        '{"unknown": 1}',
        '{"touch": "x"}',
        '{"key": 5}',
        '{"text": 5}',
        '{"lag": "slow"}',
        '{"lag": -1}',
        '{"lag": true}',
        '{"lag": 1e999}',
        '{"resync": false}',
        "[" * 50_000,
    ]
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 2)
        for text in junk:
            await ws.send_str(text)
        await ws.send_bytes(b"\x00\x01\x02" * 100)
        await ws.send_bytes(b"")
        await ws.send_json({"key": {"key": "POWER", "press": "short"}})
        await wait_until(lambda: ("key", "POWER", "short") in running.received(), "a valid event after the junk")
        await receive_frames(ws, 5)
        assert not ws.closed
    await running.settled()


async def test_a_message_over_the_limit_closes_that_socket_only(running: Running) -> None:
    async with ClientSession() as session:
        async with session.ws_connect(running.url("/ws")) as ws:
            await receive_media(ws, 1)
            await ws.send_str(json.dumps({"text": "x" * 200_000}))
            while True:
                message = await receive(ws)
                if message.type is not WSMsgType.BINARY:
                    break
            assert message.type is WSMsgType.CLOSE
            assert message.data == 1009
        await running.settled()
        async with session.ws_connect(running.url("/ws")) as ws:
            await receive_media(ws, 2)
    await running.settled()


async def test_closing_the_socket_closes_both_streams(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 2)
        await ws.send_json({"touch": TOUCH})  # a finger is down when the page goes away
        await wait_until(lambda: running.received() == [("touch", "down", 10, 20, (480, 800))], "the finger to be down")
        assert len(await running.viewers()) == 1
    await wait_until(lambda: ("release",) in running.received(), "the input stream to be closed")
    assert running.received() == [
        ("touch", "down", 10, 20, (480, 800)),
        ("touch", "up", 10, 20, (480, 800)),
        ("release",),
    ]
    await wait_until(lambda: _no_viewers(running), "the video stream to be closed")
    await running.settled()


async def _no_viewers(running: Running) -> bool:
    return not await running.viewers()


async def test_many_connections_leave_nothing_behind(running: Running) -> None:
    async with ClientSession() as session:
        for _ in range(5):
            async with session.ws_connect(running.url("/ws")) as ws:
                await receive_media(ws, 1)
                await ws.send_json({"key": {"key": "HOME", "press": "short"}})
        async with session.ws_connect(running.url("/ws")) as ws:  # one that never sends input
            await receive_media(ws, 1)
    await running.settled()
    await wait_until(lambda: _no_viewers(running), "every viewer to be gone")
    await wait_until(lambda: running.received().count(("release",)) == 5, "every input stream to have been closed")
    assert running.received().count(("release",)) == 5


async def test_a_view_only_hmi_answers_input_with_an_error_and_keeps_the_picture() -> None:
    with a_hmi(control=False) as (_, client):
        runner, port = await run_server(client)
        try:
            async with ClientSession() as session:
                async with session.get(url_of(runner, port, "/info")) as response:
                    assert (await response.json())["input"]["touch"] is False
                async with session.ws_connect(url_of(runner, port, "/ws")) as ws:
                    await receive_media(ws, 2)
                    for _ in range(3):  # each event is answered; the failed opening is not retried at once
                        await ws.send_json({"key": {"key": "HOME", "press": "short"}})
                        assert await receive_error(ws)
                    await ws.send_json({"touch": TOUCH})
                    assert await receive_error(ws)
                    await receive_frames(ws, 5)
                    assert not ws.closed
            assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        finally:
            await runner.cleanup()


# -- the guard ---------------------------------------------------------------------------------------------------------


async def test_a_request_through_another_name_or_port_is_refused(running: Running) -> None:
    async with ClientSession() as session:
        for path in ("/", "/hmi.js", "/info", "/ws"):
            for host in (
                "evil.example",
                "evil.example:80",
                f"evil.example:{running.port}",
                f"127.0.0.1:{running.port + 1}",
                "127.0.0.1",
                "",
            ):
                async with session.get(running.url(path), headers={"Host": host}) as response:
                    assert response.status == 403, (path, host)
        for host in (f"localhost:{running.port}", f"127.0.0.1:{running.port}", f"LOCALHOST:{running.port}"):
            async with session.get(running.url("/info"), headers={"Host": host}) as response:
                assert response.status == 200, host
        with pytest.raises(WSServerHandshakeError) as refused:
            await session.ws_connect(running.url("/ws"), headers={"Host": "evil.example"})
        assert refused.value.status == 403


async def test_a_page_from_another_origin_cannot_use_the_relay(running: Running) -> None:
    async with ClientSession() as session:
        for origin in (
            "http://evil.example",
            "https://127.0.0.1",
            f"http://127.0.0.1:{running.port + 1}",
            "null",
            f"http://localhost:{running.port}",
        ):
            with pytest.raises(WSServerHandshakeError) as refused:
                await session.ws_connect(running.url("/ws"), headers={"Origin": origin})
            assert refused.value.status == 403, origin
            async with session.get(running.url("/info"), headers={"Origin": origin}) as response:
                assert response.status == 403, origin
        for origin in (
            f"http://127.0.0.1:{running.port}",
            None,
        ):  # the page's own origin; and no Origin, which is not a browser
            headers = {"Origin": origin} if origin else {}
            async with session.ws_connect(running.url("/ws"), headers=headers) as ws:
                await receive_media(ws, 1)
        own = {"Host": f"localhost:{running.port}", "Origin": f"http://localhost:{running.port}"}
        async with session.ws_connect(
            running.url("/ws"), headers=own
        ) as ws:  # the page loaded as localhost has its own origin
            await receive_media(ws, 1)
    await running.settled()
    assert not running.received()  # nothing the refused ones sent got through


# -- the session's token -----------------------------------------------------------------------------------------------


async def test_info_and_the_relay_need_the_sessions_token(running: Running) -> None:
    token = running.token
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    wrong = {
        "none": "",
        "empty": "?token=",
        "other name": f"?secret={token}",
        "flipped": f"?token={flipped}",
        "shorter": f"?token={token[:-1]}",
        "longer": f"?token={token}x",
        "other case": f"?token={token.swapcase()}" if token.swapcase() != token else "?token=x",
        "not ASCII": f"?token={quote('é' * len(token))}",
        "with a NUL": f"?token={token}%00",
        "the wrong one first": f"?token={flipped}&token={token}",
    }
    base = running.url("/")
    async with ClientSession() as session:
        for name, query in wrong.items():
            async with session.get(f"{base}info{query}") as response:
                assert response.status == 403, name
                assert token not in await response.text()
            with pytest.raises(WSServerHandshakeError) as refused:
                await session.ws_connect(f"{base}ws{query}")
            assert refused.value.status == 403, name
        async with session.get(f"{base}info?token={token}") as response:
            assert response.status == 200
        async with session.get(f"{base}info?x=1&token={token}&y=2") as response:
            assert response.status == 200
        async with session.ws_connect(f"{base}ws?token={token}") as ws:
            await receive_media(ws, 1)
        for path in ("", "hmi.js"):  # the page and its script give nothing away: they are open
            async with session.get(f"{base}{path}") as response:
                assert response.status == 200
                assert token.encode() not in await response.read()
    await running.settled()
    assert not running.received()  # nothing the refused ones tried got through


async def test_a_refused_relay_opens_no_stream(running: Running) -> None:
    async with ClientSession() as session:
        with pytest.raises(WSServerHandshakeError):
            await session.ws_connect(f"{running.url('/')}ws")  # no token
    assert running.workers.count == 0
    assert await running.viewers() == []


def test_every_session_has_a_token_of_its_own(hmi: tuple[MockHmi, HmiClient]) -> None:
    _, client = hmi
    tokens = {create_app(client)[TOKEN] for _ in range(20)}
    assert len(tokens) == 20
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{22,}", token) for token in tokens)  # 16 random bytes, URL-safe
    assert create_app(client, token="chosen")[TOKEN] == "chosen"
    with pytest.raises(ValueError, match="token"):
        create_app(client, token="")


def test_the_address_carries_the_token() -> None:
    assert view_url("127.0.0.1", 80) == "http://127.0.0.1:80/"
    assert view_url("127.0.0.1", 8080, "abc-DEF_12") == "http://127.0.0.1:8080/?token=abc-DEF_12"
    assert view_url("::1", 8080, "abc") == "http://[::1]:8080/?token=abc"
    assert view_url("127.0.0.1", 1, "a b/c?d&e") == "http://127.0.0.1:1/?token=a%20b%2Fc%3Fd%26e"


async def test_a_token_that_needs_quoting_still_matches(hmi: tuple[MockHmi, HmiClient]) -> None:
    _, client = hmi
    runner, port = await run_server(client, token="a b/c?d&e+é")
    try:
        assert runner.app[TOKEN] == "a b/c?d&e+é"
        url = view_url("127.0.0.1", port, runner.app[TOKEN]).replace(f":{port}/", f":{port}/ws")
        async with ClientSession() as session, session.ws_connect(url) as ws:
            await receive_media(ws, 1)
    finally:
        await runner.cleanup()


async def test_the_token_is_written_nowhere_but_the_printed_address(
    running: Running, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = running.token
    async with ClientSession() as session:
        async with session.get(running.url("/info")) as response:
            assert response.status == 200
        async with session.get(f"{running.url('/')}info?token=wrong") as response:
            assert response.status == 403
        async with session.ws_connect(running.url("/ws")) as ws:
            await receive_media(ws, 2)
            await ws.send_json({"touch": {**TOUCH, "w": 1, "h": 1}})  # an error the relay answers (and may log)
            assert await receive_error(ws)
        with pytest.raises(WSServerHandshakeError):
            await session.ws_connect(f"{running.url('/')}ws?token=nope")
    await running.settled()
    assert [
        record.name for record in caplog.records if record.name == "aiohttp.access"
    ] == []  # it would log every query
    for record in caplog.records:
        assert token not in record.getMessage(), record
        assert token not in repr(record.args), record
        assert token not in (record.exc_text or ""), record


# -- where it listens --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "", "::", "example.com", "192.168.1.10", "10.0.0.1", "evil.localhost", "127.0.0.1.example.com", "1"],
)
def test_the_view_never_listens_beyond_this_machine(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        serve_view(as_client(object()), host=host, open_browser=False)
    with pytest.raises(ValueError, match="loopback"):
        bind_address(host)


@pytest.mark.parametrize(
    ("host", "address"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "127.0.0.1"),
        ("LocalHost", "127.0.0.1"),
        ("::1", "::1"),
        ("[::1]", "::1"),
        ("127.0.0.2", "127.0.0.2"),
    ],
)
def test_loopback_addresses_are_accepted(host: str, address: str) -> None:
    assert bind_address(host) == address


@pytest.mark.parametrize("port", [-1, 65536, "80", None, True])
def test_a_port_that_is_not_one_is_refused(port: Any) -> None:
    with pytest.raises(ValueError, match="port"):
        serve_view(as_client(object()), port=port, open_browser=False)


async def test_the_server_listens_on_127_0_0_1_only(hmi: tuple[MockHmi, HmiClient]) -> None:
    _, client = hmi
    with pytest.raises(ValueError, match="loopback"):
        await run_server(client, "0.0.0.0", 0)
    for host in ("127.0.0.1", "localhost"):
        runner, port = await run_server(client, host, 0)
        try:
            assert runner.addresses == [("127.0.0.1", port)]
            assert port > 0
            async with ClientSession() as session, session.get(f"http://127.0.0.1:{port}/") as response:
                assert response.status == 200
        finally:
            await runner.cleanup()


def token_of(url: str) -> str:
    """The token of a view's address (which has nothing else in its query)."""
    query = parse_qs(urlsplit(url).query)
    assert list(query) == ["token"], url
    return query["token"][0]


async def test_serve_prints_the_url_opens_the_browser_and_stops(
    hmi: tuple[MockHmi, HmiClient], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, client = hmi
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    async with ClientSession() as session:
        async with anyio.create_task_group() as group:
            group.start_soon(_serve, client, "127.0.0.1", 0, True)
            await wait_until(lambda: opened, "the browser to be opened")
            url = opened[0]
            assert re.fullmatch(r"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]{22}", url), url
            token = token_of(url)
            printed = capsys.readouterr().out
            assert printed.count(url) == 1  # the browser is opened at the address that is printed
            assert printed.count(token) == 1  # and nowhere else is the token written
            async with session.get(url) as response:
                assert response.status == 200
            base = url.split("?")[0]
            async with session.get(f"{base}info") as response:
                assert response.status == 403
            async with session.get(f"{base}info?token={token}") as response:
                assert response.status == 200
            async with session.ws_connect(f"{base}ws?token={token}") as ws:
                await receive_media(ws, 1)
            group.cancel_scope.cancel()
        with pytest.raises(ClientConnectorError):
            await session.get(url)
    # and without the browser; another session, another token
    opened.clear()
    async with anyio.create_task_group() as group:
        group.start_soon(_serve, client, "127.0.0.1", 0, False)
        printed = await wait_until(lambda: capsys.readouterr().out, "the URL to be printed")
        group.cancel_scope.cancel()
    assert opened == []
    second = re.search(r"http://127\.0\.0\.1:\d+/\?token=\S+", printed)
    assert second
    assert token_of(second.group()) != token


# -- stubs: what a mock cannot make the stream do ----------------------------------------------------------------------

CODEC_LINE = json.dumps(
    {"codec": "avc1.42C01F", "width": 100, "height": 200, "fps": 30, "format": "annexb", "timestamp": "us", "v": 1}
)


class StubVideo:
    """A video stream that says what it is told to: frames from ``source``, then an end with ``end_reason``."""

    def __init__(
        self,
        source: Callable[[], str | bytes | None],
        end_reason: str | None = None,
        end_error: BaseException | None = None,
    ) -> None:
        self.source = source
        self.end_reason = end_reason
        self.end_error = end_error
        self.reads = 0
        self.calls: list[tuple[str, Any, int]] = []
        self.read_threads: set[int] = set()

    def read_frame(self, timeout: float | None = None) -> str | bytes | None:
        self.read_threads.add(threading.get_ident())
        self.reads += 1
        frame = self.source()
        if frame is None:
            return None
        return frame

    def request_resync(self) -> None:
        self.calls.append(("resync", None, threading.get_ident()))

    def report_lag(self, lag_s: float) -> None:
        self.calls.append(("lag", lag_s, threading.get_ident()))


class StubInput:
    def __init__(self) -> None:
        self.sent: list[tuple[dict, int]] = []
        self.errors: list[str] = []
        self.closed = threading.Event()

    def send(self, event: dict) -> None:
        self.sent.append((event, threading.get_ident()))

    def read_error(self, timeout: float | None = None) -> str | None:
        deadline = time.monotonic() + (timeout or 0)
        while time.monotonic() < deadline:
            if self.closed.is_set():
                return None
            if self.errors:
                return self.errors.pop(0)
            time.sleep(0.005)
        if self.closed.is_set():
            return None
        raise TimeoutError


class _OpenInput:
    def __init__(self, stream: StubInput) -> None:
        self.stream = stream

    def __enter__(self) -> StubInput:
        return self.stream

    def __exit__(self, *exc: object) -> None:
        self.stream.closed.set()


class StubClient:
    """A client that opens ``video`` (or raises ``refusal``, or waits for ``release_open``) and ``input``."""

    def __init__(
        self, video: StubVideo | None = None, refusal: BaseException | None = None, block_open: bool = False
    ) -> None:
        self.stream = video
        self.refusal = refusal
        self.block_open = block_open
        self.release_open = threading.Event()
        self.opening = threading.Event()
        self.video_open = False
        self.video_closed = threading.Event()
        self.input_stream = StubInput()
        self.input_opened = 0

    @contextmanager
    def video(self, timeout: float | None = None) -> Generator[StubVideo]:
        self.opening.set()
        if self.block_open:
            self.release_open.wait(DEADLINE_S)
        if self.refusal is not None:
            raise self.refusal
        assert self.stream is not None
        self.video_open = True
        try:
            yield self.stream
        finally:
            self.video_open = False
            self.video_closed.set()

    def input(self, on_error: Callable[[str], None] | None = None) -> _OpenInput:
        """Not a generator: only its ``__exit__`` closes it (a dropped generator would close itself)."""
        self.input_opened += 1
        return _OpenInput(self.input_stream)

    def hmi_info(self) -> dict:
        return {"input": {"touch": True}}


def frames_of(*items: str | bytes | None) -> Callable[[], str | bytes | None]:
    queue = list(items)
    return lambda: queue.pop(0) if queue else None


async def stub_server(client: StubClient) -> tuple[web.AppRunner, int]:
    return await run_server(as_client(client), "127.0.0.1", 0)


async def close_code(ws: Any) -> tuple[int, str]:
    """The close code and reason the server ended the socket with (pictures before it are skipped)."""
    while True:
        message = await receive(ws)
        if message.type is WSMsgType.CLOSE:
            return message.data, message.extra
        assert message.type in (WSMsgType.TEXT, WSMsgType.BINARY), message


@pytest.mark.parametrize(
    ("end_reason", "end_error", "code"),
    [
        ("ended", None, 1000),
        (None, None, 1000),
        ("error", RuntimeError("the exporter fell over"), 1011),
        ("refused", None, 4403),
    ],
)
async def test_the_close_code_says_how_the_stream_ended(
    end_reason: str | None, end_error: BaseException | None, code: int
) -> None:
    media = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 10
    client = StubClient(StubVideo(frames_of(CODEC_LINE, media, media), end_reason, end_error))
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            assert (await receive(ws)).data == CODEC_LINE  # unchanged
            assert (await receive(ws)).data == media
            assert (await receive(ws)).data == media
            got, reason = await close_code(ws)
            assert got == code
            assert reason
            if code == 1011:
                assert "fell over" in reason
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        assert client.video_closed.is_set()
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(
    ("refusal", "code"),
    [
        (HmiStreamClosedError("not allowed", reason="refused"), 4403),
        (HmiStreamClosedError("the stream could not be opened: boom", reason="error", detail="boom"), 1011),
        (HmiStreamClosedError("the stream ended before it began", reason="ended"), 1011),
        (TimeoutError("nothing came"), 1011),
        (RuntimeError("anything"), 1011),
    ],
)
async def test_a_stream_that_cannot_be_opened_closes_the_socket(refusal: BaseException, code: int) -> None:
    client = StubClient(refusal=refusal)
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            got, reason = await close_code(ws)
            assert got == code
            assert 0 < len(reason.encode()) <= 123
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
    finally:
        await runner.cleanup()


async def test_a_stream_the_exporter_cannot_start_closes_the_socket_with_1011(
    hmi: tuple[MockHmi, HmiClient],
) -> None:
    driver, client = hmi
    fake_source(driver).fail_start = "the device is not there"
    runner, port = await run_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            got, reason = await close_code(ws)
            assert got == 1011
            assert reason
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
    finally:
        await runner.cleanup()


async def test_the_client_is_never_called_on_the_event_loop() -> None:
    loop_thread = threading.get_ident()
    media = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 10
    ticks = iter(range(10**9))

    def source() -> str | bytes:
        time.sleep(0.005)
        return CODEC_LINE if next(ticks) == 0 else media

    video = StubVideo(source)
    client = StubClient(video)
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            await receive_media(ws, 2)
            await ws.send_json({"resync": True})
            await ws.send_json({"lag": 0.4})
            await ws.send_json({"touch": TOUCH})
            await ws.send_json({"key": {"key": "HOME", "press": "long"}})
            await ws.send_json({"text": "typed"})
            await wait_until(
                lambda: len(video.calls) == 2 and len(client.input_stream.sent) == 3, "the calls to arrive"
            )
            assert [call[:2] for call in video.calls] == [("resync", None), ("lag", 0.4)]
            assert [event for event, _ in client.input_stream.sent] == [
                {"touch": TOUCH},
                {"key": {"key": "HOME", "press": "long"}},
                {"text": "typed"},
            ]
            threads = (
                {call[2] for call in video.calls}
                | {thread for _, thread in client.input_stream.sent}
                | video.read_threads
            )
            assert loop_thread not in threads
            assert client.input_opened == 1  # opened on the first event, once
        await wait_until(client.input_stream.closed.is_set, "the input stream to be closed")
        await wait_until(client.video_closed.is_set, "the video stream to be closed")
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
    finally:
        await runner.cleanup()


async def test_what_the_exporter_answers_an_input_with_reaches_the_page() -> None:
    media = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 10
    ticks = iter(range(10**9))

    def source() -> str | bytes:
        time.sleep(0.005)
        return CODEC_LINE if next(ticks) == 0 else media

    client = StubClient(StubVideo(source))
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            await receive_media(ws, 1)
            await ws.send_json({"key": {"key": "HOME", "press": "short"}})
            client.input_stream.errors.append("the device is busy")
            assert await receive_error(ws) == "the device is busy"
    finally:
        await runner.cleanup()


async def test_a_slow_browser_slows_the_reader() -> None:
    big = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 256 * 1024
    ticks = iter(range(10**9))
    video = StubVideo(lambda: CODEC_LINE if next(ticks) == 0 else big)
    client = StubClient(video)
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
            # the browser reads nothing: the reader fills the socket's buffers and then waits
            seen = {"reads": -1, "since": time.monotonic()}

            def stalled() -> bool:
                if video.reads != seen["reads"]:
                    seen["reads"], seen["since"] = video.reads, time.monotonic()
                return time.monotonic() - seen["since"] > 0.6

            await wait_until(stalled, "the reader to stop at a full socket", timeout=60.0)
            held = video.reads
            assert 1 <= held < 400, f"the reader took {held} pictures of 256 kB that nobody was reading"
            await anyio.sleep(0.3)
            assert video.reads == held
            for _ in range(20):  # reading again lets it go on
                assert (await receive(ws)).type in (WSMsgType.TEXT, WSMsgType.BINARY)
            await wait_until(lambda: video.reads > held, "the reader to go on once the browser reads")
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
    finally:
        await runner.cleanup()


async def test_leaving_while_the_stream_is_still_opening_neither_hangs_nor_leaks() -> None:
    media = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 10
    client = StubClient(StubVideo(frames_of(CODEC_LINE, media)), block_open=True)
    runner, port = await stub_server(client)
    try:
        async with ClientSession() as session:
            async with session.ws_connect(url_of(runner, port, "/ws")) as ws:
                await to_thread.run_sync(client.opening.wait, DEADLINE_S)
                assert runner.app[WORKERS].count == 1
                assert not ws.closed
            # the page is gone while the exporter is still starting the device: the connection ends at once
            await wait_until(
                lambda: not runner.app[SOCKETS], "the connection to end without waiting for the stream to open"
            )
            assert runner.app[WORKERS].count == 1
        client.release_open.set()  # the stream opens at last: nobody wants it, it is closed again
        assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        assert client.video_closed.is_set()
        assert not client.video_open
    finally:
        client.release_open.set()
        await runner.cleanup()


async def test_stopping_the_server_ends_the_connections() -> None:
    media = b"\x00" * 8 + START_CODE + b"\x41\x9a" + b"\xaa" * 10
    ticks = iter(range(10**9))

    def source() -> str | bytes:
        time.sleep(0.005)
        return CODEC_LINE if next(ticks) == 0 else media

    client = StubClient(StubVideo(source))
    runner, port = await stub_server(client)
    async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
        await receive_media(ws, 2)
        with anyio.fail_after(DEADLINE_S):
            await runner.cleanup()
        got, _ = await close_code(ws)
        assert got == 1001
    assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
    assert client.video_closed.is_set()
    assert client.input_stream.closed.is_set() or client.input_opened == 0


async def test_the_server_can_listen_on_the_ipv6_loopback(hmi: tuple[MockHmi, HmiClient]) -> None:
    _, client = hmi
    try:
        runner, port = await run_server(client, "::1", 0)
    except OSError:
        pytest.skip("this machine has no IPv6 loopback")  # ty: ignore[too-many-positional-arguments]
    try:
        assert runner.addresses[0][0] == "::1"
        async with ClientSession() as session:
            async with session.get(f"http://[::1]:{port}/") as response:
                assert response.status == 200
                assert f"ws://[::1]:{port}" in response.headers["Content-Security-Policy"]
            async with session.get(f"http://[::1]:{port}/", headers={"Host": f"evil.example:{port}"}) as response:
                assert response.status == 403
            url = url_of(runner, port, "/ws", host="[::1]")
            async with session.ws_connect(url, headers={"Origin": f"http://[::1]:{port}"}) as ws:
                await receive_media(ws, 1)
    finally:
        await runner.cleanup()


async def test_info_that_cannot_be_read_is_a_bad_gateway() -> None:
    class Failing(StubClient):
        def hmi_info(self) -> dict:
            raise RuntimeError("the exporter is gone")

    runner, port = await stub_server(Failing())
    try:
        async with ClientSession() as session, session.get(url_of(runner, port, "/info")) as response:
            assert response.status == 502
            assert await response.json() == {"error": "the exporter is gone"}
    finally:
        await runner.cleanup()


def test_create_app_routes() -> None:
    app = create_app(as_client(StubClient()))
    assert {route.resource.canonical for route in app.router.routes() if route.resource is not None} == {
        "/",
        "/info",
        "/hmi.js",
        "/ws",
    }


# -- the page's own code -----------------------------------------------------------------------------------------------

NODE = shutil.which("node")


def node() -> str:
    assert NODE is not None
    return NODE


@pytest.fixture
def module(tmp_path: Path) -> Path:
    """The page's script as a ``.mjs`` file, which node reads as a module whatever its version."""
    target = tmp_path / "hmi.mjs"
    target.write_bytes((STATIC / "hmi.js").read_bytes())
    return target


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_script_has_no_syntax_errors(module: Path) -> None:
    result = subprocess.run([node(), "--check", str(module)], capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr


def lag_sequence() -> list[tuple[float, int]]:
    """25 s of pictures at 30 a second, a path that falls behind by half a second a second from the 8th; one replay."""
    pictures = []
    for i in range(25 * 30):
        now = i / 30
        behind = max(0.0, now - 8) * 0.5
        pictures.append((now, round((now - behind) * 1e6) + 1_700_000_000_000_000))
    pictures.insert(300, (10.0, pictures[250][1]))  # an old picture again, as a resync gives
    return pictures


#: What the page's pure functions must do, checked under node. The values after ``__`` are filled in from this side.
PURE_FUNCTIONS = r"""
import assert from "node:assert/strict";
import * as page from "__MODULE__";

const hex = (h) => Uint8Array.from(Buffer.from(h, "hex"));

// the codec line
const line = page.parseCodecLine(
  '{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90,"coded_width":480,"coded_height":816}',
);
assert.deepEqual(line, {
  codec: "avc1.42C01F", width: 480, height: 800, codedWidth: 480, codedHeight: 816, rotation: 90, fps: 30,
});
assert.deepEqual(page.shownSize(line), [800, 480]);
assert.deepEqual(page.shownSize({ ...line, rotation: 180 }), [480, 800]);
assert.deepEqual(page.shownSize({ ...line, rotation: 270 }), [800, 480]);
const plain = page.parseCodecLine('{"codec":"avc1.64001F","width":100,"height":200}');
assert.equal(plain.codedWidth, 100);
assert.equal(plain.codedHeight, 200);
assert.equal(plain.rotation, 0);
assert.equal(plain.fps, 0);
assert.equal(page.parseCodecLine("not json"), null);
assert.equal(page.parseCodecLine('{"codec":"x","width":0,"height":5}'), null);
assert.equal(page.parseCodecLine('{"width":5,"height":5}'), null);
assert.equal(page.parseCodecLine('{"codec":"x","width":5.5,"height":5}'), null);
assert.equal(page.parseCodecLine("null"), null);
assert.equal(page.parseCodecLine('{"codec":"x","width":5,"height":5,"rotation":45}').rotation, 0);
assert.deepEqual(page.parseServerText('{"error":"nope"}'), { kind: "error", message: "nope" });
assert.equal(page.parseServerText('{"codec":"avc1.1","width":4,"height":4}').kind, "line");
assert.equal(page.parseServerText("[]"), null);
assert.equal(page.parseServerText("garbage"), null);

// what restarts the decoder, how it is configured, how the picture is drawn
assert.equal(page.restartsDecoder(null, line), true);
assert.equal(page.restartsDecoder(line, { ...line, rotation: 0, fps: 60 }), false);
assert.equal(page.restartsDecoder(line, { ...line, codec: "avc1.640028" }), true);
assert.equal(page.restartsDecoder(line, { ...line, codedHeight: 800 }), true);
assert.deepEqual(page.decoderConfig(line), {
  codec: "avc1.42C01F", codedWidth: 480, codedHeight: 816, optimizeForLatency: true,
});
const plan = page.canvasPlan(line);
assert.equal(plan.width, 800);
assert.equal(plan.height, 480);
assert.equal(plan.srcW, 480);
assert.equal(plan.srcH, 800);
assert.ok(Math.abs(plan.angle - Math.PI / 2) < 1e-12);

// frames: the NAL types agree with the exporter-side parser's
const au = hex("__ACCESS_UNIT__");
const delta = hex("__DELTA__");
const sei = hex("__WITH_SEI__");
assert.deepEqual(page.nalTypes(au), __ACCESS_UNIT_TYPES__);
assert.deepEqual(page.nalTypes(delta), __DELTA_TYPES__);
assert.deepEqual(page.nalTypes(sei), __WITH_SEI_TYPES__);
assert.equal(page.hasIdr(au), true);
assert.equal(page.hasIdr(delta), false);
assert.equal(page.hasIdr(sei), false);
assert.equal(page.hasIdr(new Uint8Array(0)), false);
assert.equal(page.hasIdr(hex("0000000109")), false);
const frame = hex("__MEDIA__");
const split = page.splitMedia(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
assert.equal(split.ptsUs, 1700000123456789);
assert.deepEqual(Array.from(split.data), Array.from(au));
assert.equal(page.splitMedia(new ArrayBuffer(8)), null);
assert.equal(page.splitMedia("text"), null);

// the pointer: a 480 x 800 picture shown at half size, at (100, 50)
const rect = { left: 100, top: 50, width: 240, height: 400 };
assert.deepEqual(page.mapPoint(100, 50, rect, 480, 800), { x: 0, y: 0 });
assert.deepEqual(page.mapPoint(220, 250, rect, 480, 800), { x: 240, y: 400 });
assert.deepEqual(page.mapPoint(340, 450, rect, 480, 800), { x: 479, y: 799 });
assert.deepEqual(page.mapPoint(-30, 9999, rect, 480, 800), { x: 0, y: 799 });
// a turned picture is 800 x 480: the scale is by the shown size
assert.deepEqual(page.mapPoint(160, 100, rect, 800, 480), { x: 200, y: 60 });
assert.equal(page.mapPoint(1, 1, { left: 0, top: 0, width: 0, height: 10 }, 4, 4), null);
assert.deepEqual(
  page.touchMessage("down", { x: 1, y: 2 }, 480, 800),
  { touch: { type: "down", x: 1, y: 2, id: 0, w: 480, h: 800 } },
);
assert.deepEqual(page.keyMessage("HOME", "long"), { key: { key: "HOME", press: "long" } });

// text and keys
assert.deepEqual(page.textMessages("hello"), [{ text: "hello" }]);
assert.deepEqual(page.textMessages("abcde", 2), [{ text: "ab" }, { text: "cd" }, { text: "e" }]);
// never half a character
assert.deepEqual(page.textMessages("a\u{1F600}b", 2), [{ text: "a\u{1F600}" }, { text: "b" }]);
assert.deepEqual(page.textMessages(""), []);
assert.deepEqual(
  page.hardKeyButtons([
    { key: "HOME", label: "Home", presses: ["short", "long"] },
    { key: "BACK", presses: ["short"] },
    { key: "POWER", label: "Power", presses: ["long"] },
    { key: "MUTE", label: "Mute", presses: ["down", "up"] },
    { label: "no key" },
    null,
    { key: "ASSIST", label: "", presses: "short" },
  ]),
  [
    { key: "HOME", label: "Home", short: true, long: true },
    { key: "BACK", label: "BACK", short: true, long: false },
    { key: "POWER", label: "Power", short: false, long: true },
    { key: "ASSIST", label: "ASSIST", short: true, long: true },
  ],
);
assert.deepEqual(page.hardKeyButtons(undefined), []);
assert.equal(page.HOLD_MS, 600);

// the keyboard on the focused picture: text, and the few named keys the screen declares
const ev = (init) => ({
  key: "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, isComposing: false, ...init,
});
const all = { text: true, keys: new Set(["ENTER", "DEL", "ESCAPE", "TAB"]) };
assert.deepEqual(page.keyboardMessage(ev({ key: "x" }), all), { text: "x" });
assert.deepEqual(page.keyboardMessage(ev({ key: "X", shiftKey: true }), all), { text: "X" });
assert.deepEqual(page.keyboardMessage(ev({ key: " " }), all), { text: " " });
assert.deepEqual(page.keyboardMessage(ev({ key: "Enter" }), all), { key: { key: "ENTER", press: "short" } });
assert.deepEqual(page.keyboardMessage(ev({ key: "Backspace" }), all), { key: { key: "DEL", press: "short" } });
assert.deepEqual(page.keyboardMessage(ev({ key: "Escape" }), all), { key: { key: "ESCAPE", press: "short" } });
assert.deepEqual(page.keyboardMessage(ev({ key: "Tab" }), all), { key: { key: "TAB", press: "short" } });
assert.equal(page.keyboardMessage(ev({ key: "Tab", shiftKey: true }), all), null); // focus can always leave backwards
// a screen that takes no text
assert.equal(page.keyboardMessage(ev({ key: "x" }), { text: false, keys: all.keys }), null);
assert.deepEqual(page.keyboardMessage(ev({ key: "Enter" }), { text: false, keys: all.keys }), {
  key: { key: "ENTER", press: "short" },
});
assert.equal(page.keyboardMessage(ev({ key: "Enter" }), { text: true, keys: new Set() }), null); // not declared
assert.equal(page.keyboardMessage(ev({ key: "Enter" }), { text: true, keys: new Set(["TAB"]) }), null);
assert.equal(page.keyboardMessage(ev({ key: "x", ctrlKey: true }), all), null);
assert.equal(page.keyboardMessage(ev({ key: "x", metaKey: true }), all), null);
assert.equal(page.keyboardMessage(ev({ key: "x", altKey: true }), all), null);
assert.equal(page.keyboardMessage(ev({ key: "Enter", ctrlKey: true }), all), null);
assert.equal(page.keyboardMessage(ev({ key: "x", isComposing: true }), all), null);
assert.equal(page.keyboardMessage({ ctrlKey: false }, all), null);
// a raw browser key event is never sent: what is not a character or one of the four is nothing
const others = ["ArrowUp", "Shift", "Control", "F1", "Dead", "Unidentified", "Process", "Delete", "Home"];
for (const key of [...others, "constructor", "__proto__"]) {
  assert.equal(page.keyboardMessage(ev({ key }), all), null, key);
}

// the address of /info and /ws: with the page's token
assert.equal(page.endpoint("/ws", "?token=abc-DEF_12"), "/ws?token=abc-DEF_12");
assert.equal(page.endpoint("/info", "?x=1&token=a%2Fb"), "/info?token=a%2Fb");
assert.equal(page.endpoint("/ws", ""), null);
assert.equal(page.endpoint("/ws", "?token="), null);
assert.equal(page.endpoint("/ws", "?other=1"), null);

// reconnecting
assert.deepEqual([0, 1, 2, 3, 4, 5, 50].map(page.backoffMs), [500, 1000, 2000, 4000, 8000, 8000, 8000]);
assert.equal(page.closeAdvice(4403, "refused").retry, false);
assert.equal(page.closeAdvice(1000, "").retry, true);
assert.equal(page.closeAdvice(1011, "boom").message, "The stream failed: boom");
assert.equal(page.closeAdvice(1006, "").retry, true);

// the lag: the same pictures through the exporter-side client's meter and this one give the same answers
const meter = new page.LagMeter();
const lags = __PICTURES__.map(([now, pts]) => meter.take(now, pts));
const expected = __LAGS__;
assert.equal(lags.length, expected.length);
lags.forEach((lag, i) => {
  if (expected[i] === null) assert.equal(lag, null, `picture ${i}`);
  else assert.ok(Math.abs(lag - expected[i]) < 1e-6, `picture ${i}: ${lag} != ${expected[i]}`);
});
const reporter = new page.LagReporter();
const offers = [[0, 0.3], [0.5, 0.4], [1.0, 0.5], [1.2, 0.1], [1.3, 0.05], [5, 0.6], [5.5, 0.7], [6.1, 0.0]];
assert.deepEqual(offers.map(([now, lag]) => reporter.offer(now, lag)), [0.3, null, 0.5, 0.1, null, 0.6, null, 0]);

console.log(JSON.stringify({ ok: true }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_pages_pure_functions(module: Path) -> None:
    from ..client import LagMeter

    pictures = lag_sequence()
    meter = LagMeter()
    lags = [meter.take(now, pts) for now, pts in pictures]
    assert any(lag for lag in lags if lag), "the sequence must show a lag"
    access_unit = START_CODE + make_sps(480, 800) + START_CODE + PPS + START_CODE + b"\x65\x88\x84" + b"0" * 50
    delta = START_CODE + b"\x41\x9a" + b"0" * 50
    with_sei = START_CODE + b"\x06\x05\x01" + START_CODE + b"\x41\x9a" + b"0" * 20
    media = (1_700_000_123_456_789).to_bytes(8, "big") + access_unit
    values = {
        "__MODULE__": module.as_uri(),
        "__ACCESS_UNIT_TYPES__": json.dumps(nal_types(access_unit)),
        "__DELTA_TYPES__": json.dumps(nal_types(delta)),
        "__WITH_SEI_TYPES__": json.dumps(nal_types(with_sei)),
        "__ACCESS_UNIT__": access_unit.hex(),
        "__DELTA__": delta.hex(),
        "__WITH_SEI__": with_sei.hex(),
        "__MEDIA__": media.hex(),
        "__PICTURES__": json.dumps(pictures),
        "__LAGS__": json.dumps(lags),
    }
    script = PURE_FUNCTIONS
    for token in sorted(values, key=len, reverse=True):  # the longer names first: one is the start of another
        script = script.replace(token, values[token])
    result = subprocess.run(
        [node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}


#: The page itself, run under node with just enough of a browser around it (a document, a socket, a decoder), to see
#: what it sends and draws for what it is given. The browser's own behavior (pointer capture, the real decoder) is not
#: in it.
PAGE_SCENARIO = r"""
import assert from "node:assert/strict";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let fakeNow = 1000; // milliseconds, for performance.now()
Object.defineProperty(globalThis, "performance", { value: { now: () => fakeNow }, configurable: true });

// -- a document of just enough elements -----------------------------------------------------------
class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.listeners = {};
    this.children = [];
    this.hidden = false;
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.width = 300;
    this.height = 150;
    this.rect = { left: 0, top: 0, width: 400, height: 240 };
    this.classes = new Set();
    this.classList = { add: (c) => this.classes.add(c) };
    this.calls = [];
    this.captured = null;
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  fire(type, event = {}) {
    const fired = { prevented: false, preventDefault() { this.prevented = true; }, ...event };
    for (const handler of this.listeners[type] || []) handler(fired);
    return fired;
  }
  append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
  replaceChildren() { this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  get firstElementChild() { return this.children[0]; }
  setPointerCapture(id) { this.captured = id; }
  focus() { this.focused = true; }
  getBoundingClientRect() { return this.rect; }
  getContext() {
    const calls = this.calls;
    return new Proxy({}, { get: (_, name) => (...args) => calls.push([name, ...args]) });
  }
}
const elements = {};
for (const id of ["hmi", "status", "errors", "keys", "controls", "text-form", "text-input", "reconnect"]) {
  elements[id] = new FakeElement(id);
}
elements.controls.hidden = true;
elements["text-form"].hidden = true;
elements.reconnect.hidden = true;
const documentListeners = new FakeElement("document");
globalThis.document = {
  getElementById: (id) => elements[id],
  createElement: (tag) => new FakeElement(tag),
  addEventListener: (type, handler) => documentListeners.addEventListener(type, handler),
  activeElement: null,
};
globalThis.window = new FakeElement("window");
globalThis.location = { protocol: "http:", host: "127.0.0.1:4321", search: "__SEARCH__" };
globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);

// -- a socket, a decoder, and /info ---------------------------------------------------------------
const sockets = [];
globalThis.WebSocket = class {
  static OPEN = 1;
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen(); }
  receive(data) { this.onmessage({ data }); }
  hangUp(code, reason = "") { this.readyState = 3; this.onclose({ code, reason }); }
};
const decoders = [];
globalThis.VideoDecoder = class {
  static async isConfigSupported() { return { supported: true }; }
  constructor(init) {
    this.init = init; this.state = "unconfigured"; this.decodeQueueSize = 0; this.configs = []; this.chunks = [];
    decoders.push(this);
  }
  configure(config) { this.state = "configured"; this.configs.push(config); }
  reset() { this.state = "unconfigured"; }
  decode(chunk) {
    this.chunks.push(chunk);
    this.init.output({ displayWidth: 480, displayHeight: 800, close() { this.closed = true; } });
  }
};
globalThis.EncodedVideoChunk = class { constructor(init) { Object.assign(this, init); } };
const info = {
  input: {
    touch: true, text: true,
    keys: ["HOME", "BACK", "ENTER", "DEL"],
    hard_keys: [
      { key: "HOME", label: "Home", presses: ["short", "long"] },
      { key: "POWER", label: "Power", presses: ["long"] },
    ],
  },
};
const fetched = [];
globalThis.fetch = async (url) => { fetched.push(url); return { ok: true, json: async () => info }; };

// -- frames ---------------------------------------------------------------------------------------
const start = [0, 0, 0, 1];
const keyUnit = Uint8Array.from([...start, 0x67, 1, 2, ...start, 0x68, 3, ...start, 0x65, 4, 5, 6]);
const deltaUnit = Uint8Array.from([...start, 0x41, 9, 9, 9]);
function media(ptsUs, unit) {
  const buffer = new ArrayBuffer(8 + unit.length);
  const view = new DataView(buffer);
  view.setUint32(0, Math.floor(ptsUs / 4294967296));
  view.setUint32(4, ptsUs % 4294967296);
  new Uint8Array(buffer, 8).set(unit);
  return buffer;
}

await import("__MODULE__");
const canvas = elements.hmi;
if ("__SEARCH__" === "") { // opened without the token the address printed: it connects to nothing and says why
  assert.equal(sockets.length, 0);
  assert.equal(fetched.length, 0);
  assert.match(elements.status.textContent, /no token/);
  console.log(JSON.stringify({ ok: true }));
  process.exit(0);
}
assert.equal(sockets.length, 1);
assert.equal(sockets[0].url, "ws://127.0.0.1:4321/ws?token=abc-DEF_12"); // the page's token goes on to the socket
assert.equal(elements.status.textContent, "Connecting…");
let ws = sockets[0];
ws.open();
assert.match(elements.status.textContent, /Waiting for the first picture/);

// the codec line sizes the canvas (turned 90 degrees: 800 x 480) and starts the decoder
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90}');
assert.equal(canvas.width, 800);
assert.equal(canvas.height, 480);
assert.equal(canvas.hidden, false);
assert.equal(decoders.length, 1);
assert.deepEqual(decoders[0].configs, [
  { codec: "avc1.42C01F", codedWidth: 480, codedHeight: 800, optimizeForLatency: true },
]);

// a delta picture before the first key picture is dropped; the key picture and what follows are decoded
ws.receive(media(1_700_000_000_000_000, deltaUnit));
assert.equal(decoders[0].chunks.length, 0);
ws.receive(media(1_700_000_000_033_000, keyUnit));
ws.receive(media(1_700_000_000_066_000, deltaUnit));
assert.deepEqual(decoders[0].chunks.map((c) => c.type), ["key", "delta"]);
assert.equal(decoders[0].chunks[0].timestamp, 1_700_000_000_033_000);
assert.match(elements.status.textContent, /Live: 800×480, turned 90°/);

// the picture is drawn turned about its middle
const drawn = canvas.calls.filter(([name]) => name === "drawImage");
assert.equal(drawn.length, 2);
assert.deepEqual(canvas.calls.find(([name]) => name === "translate"), ["translate", 400, 240]);
assert.ok(Math.abs(canvas.calls.find(([name]) => name === "rotate")[1] - Math.PI / 2) < 1e-12);
assert.deepEqual(drawn[0].slice(2), [-240, -400, 480, 800]);

// what /info says is offered: the hard keys as buttons, and the text box
await sleep(20);
// and to /info (when the page starts, and again when a connection gets its first picture)
assert.ok(fetched.length >= 1 && fetched.every((url) => url === "/info?token=abc-DEF_12"), JSON.stringify(fetched));
assert.equal(elements.controls.hidden, false);
assert.equal(elements["text-form"].hidden, false);
assert.deepEqual(elements.keys.children.map((b) => b.textContent), ["Home", "Power"]);
const [home, power] = elements.keys.children;

// the primary pointer is the one finger; the picture is shown at 400 x 240 for 800 x 480: a point is twice the pixel
const touch = (type, x, y) => ({ touch: { type, x, y, id: 0, w: 800, h: 480 } });
const pointer = (extra) => ({ isPrimary: true, pointerType: "mouse", button: 0, pointerId: 1, ...extra });
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 200, clientY: 120 }));
canvas.fire("pointerdown", pointer({ pointerId: 2, isPrimary: false, clientX: 1, clientY: 1 })); // a second contact
canvas.fire("pointermove", pointer({ pointerId: 2, isPrimary: false, clientX: 2, clientY: 2 }));
canvas.fire("pointermove", pointer({ clientX: 210, clientY: 120 }));
canvas.fire("pointermove", pointer({ clientX: 220, clientY: 121 }));
canvas.fire("pointermove", pointer({ clientX: 230, clientY: 122 }));
await sleep(30); // one animation frame: the three moves are one
canvas.fire("pointerup", pointer({ clientX: 240, clientY: 123 }));
assert.deepEqual(ws.sent, [touch("down", 400, 240), touch("move", 460, 244), touch("up", 480, 246)]);
assert.equal(canvas.captured, 1);
canvas.fire("lostpointercapture", pointer({})); // after the up: nothing is down, so nothing is cancelled
canvas.fire("pointerdown", pointer({ button: 2, clientX: 5, clientY: 5 })); // not the primary button
assert.equal(ws.sent.length, 3);
canvas.fire("pointerdown", pointer({ clientX: 100, clientY: 60 }));
canvas.fire("lostpointercapture", pointer({}));
assert.deepEqual(ws.sent.slice(3), [touch("down", 200, 120), touch("cancel", 200, 120)]);

// hard keys: a click is short, held for 600 ms it is long, and a key with only a long press needs it held
ws.sent.length = 0;
home.fire("pointerdown", pointer({}));
home.fire("pointerup", pointer({}));
home.fire("click", { detail: 1 }); // the click that follows a pointer: nothing more
home.fire("pointerdown", pointer({}));
await sleep(700);
home.fire("pointerup", pointer({}));
power.fire("pointerdown", pointer({}));
power.fire("pointerup", pointer({}));
power.fire("click", { detail: 0 }); // from the keyboard
home.fire("click", { detail: 0 });
assert.deepEqual(ws.sent, [
  { key: { key: "HOME", press: "short" } },
  { key: { key: "HOME", press: "long" } },
  { key: { key: "POWER", press: "long" } },
  { key: { key: "HOME", press: "short" } },
]);

// text, from the box and pasted onto the focused picture
ws.sent.length = 0;
elements["text-input"].value = "hello";
elements["text-form"].fire("submit");
assert.equal(elements["text-input"].value, "");
document.activeElement = canvas;
documentListeners.fire("paste", { clipboardData: { getData: () => "pasted" } });
assert.deepEqual(ws.sent, [{ text: "hello" }, { text: "pasted" }]);

// typing on the focused picture: a character is text, Enter and Backspace are keys the screen declares; the rest is the
// browser's
ws.sent.length = 0;
const typed = (init) => canvas.fire("keydown", {
  key: "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init,
});
assert.equal(typed({ key: "a" }).prevented, true);
assert.equal(typed({ key: "A", shiftKey: true }).prevented, true);
assert.equal(typed({ key: " " }).prevented, true);
assert.equal(typed({ key: "é" }).prevented, true);
assert.equal(typed({ key: "Enter" }).prevented, true);
assert.equal(typed({ key: "Backspace" }).prevented, true);
assert.deepEqual(ws.sent, [
  { text: "a" },
  { text: "A" },
  { text: " " },
  { text: "é" },
  { key: { key: "ENTER", press: "short" } },
  { key: { key: "DEL", press: "short" } },
]);
ws.sent.length = 0;
const left = [
  typed({ key: "v", ctrlKey: true }), // a shortcut
  typed({ key: "c", metaKey: true }),
  typed({ key: "x", altKey: true }),
  typed({ key: "a", isComposing: true }), // the IME's
  typed({ key: "Process", isComposing: true }),
  typed({ key: "Dead" }), // not one character
  typed({ key: "Shift" }),
  typed({ key: "ArrowLeft" }),
  typed({ key: "F5" }),
  typed({ key: "Tab" }), // the screen does not declare TAB or ESCAPE: the browser keeps them (focus moves on)
  typed({ key: "Escape" }),
  typed({ key: "Enter", ctrlKey: true }),
];
assert.deepEqual(ws.sent, []);
assert.deepEqual(left.map((e) => e.prevented), left.map(() => false));

// an error from the exporter is shown, and only the last few are kept
for (const message of ["one", "two", "three", "four"]) ws.receive(JSON.stringify({ error: message }));
assert.deepEqual(elements.errors.children.map((e) => e.textContent), ["two", "three", "four"]);

// a decoder that errors: the page asks for a key picture and starts a new decoder
ws.sent.length = 0;
decoders[0].init.error(new Error("broken"));
assert.deepEqual(ws.sent, [{ resync: true }]);
assert.equal(decoders.length, 1); // the fake is not "closed": the same decoder is reset and configured again
assert.equal(decoders[0].configs.length, 2);
ws.receive(media(1_700_000_000_099_000, deltaUnit)); // a delta picture after a reconfigure waits for a key picture
assert.equal(decoders[0].chunks.length, 2);

// the lag: pictures that arrive later and later are reported (once a second at most), and again when it is gone
ws.sent.length = 0;
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90}');
const pictures = [];
for (let i = 0; i < 240; i++) {
  const seconds = i / 30;
  fakeNow = 1000 + seconds * 1000;
  const late = seconds > 4 ? (seconds - 4) * 0.8 : 0; // the path falls behind from the fourth second
  ws.receive(media(Math.round((1_700_000_000 + seconds - late) * 1e6), i % 30 === 0 ? keyUnit : deltaUnit));
}
const lags = ws.sent.filter((m) => "lag" in m).map((m) => m.lag);
assert.ok(lags.length >= 2 && lags.length <= 5, JSON.stringify(lags));
assert.ok(lags.every((lag) => lag > 0.25), JSON.stringify(lags));
assert.ok(lags[lags.length - 1] > lags[0], JSON.stringify(lags));
ws.sent.length = 0;
for (let i = 240; i < 300; i++) { // it catches up
  fakeNow = 1000 + (i / 30) * 1000;
  ws.receive(media(Math.round((1_700_000_000 + 8 - 3.2 + (i - 240) / 30 * 1.5) * 1e6), deltaUnit));
}

// a socket that closes is opened again, after a pause; a refusal is not
fakeNow = 20_000;
ws.hangUp(1011, "the screen stream broke");
assert.match(elements.status.textContent, /The stream failed: the screen stream broke\. Reconnecting in 1 s/);
assert.equal(elements.reconnect.hidden, false);
await sleep(700);
assert.equal(sockets.length, 2);
ws = sockets[1];
ws.open();
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800}');
assert.equal(canvas.width, 480);
assert.equal(decoders[0].configs.length >= 3, true); // a new connection starts the decoder again
ws.hangUp(4403, "refused");
assert.match(elements.status.textContent, /The exporter refused the stream: refused/);
await sleep(1200);
assert.equal(sockets.length, 2);
elements.reconnect.fire("click");
assert.equal(sockets.length, 3);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_decodes_draws_and_sends_what_it_should(module: Path) -> None:
    assert "__MODULE__" in PAGE_SCENARIO  # it runs the script under test, not a copy of it
    assert "/tmp" not in PAGE_SCENARIO
    for search in ("?token=abc-DEF_12", ""):  # with the token of the address, and without
        script = PAGE_SCENARIO.replace("__MODULE__", module.as_uri()).replace("__SEARCH__", search)
        result = subprocess.run(
            [node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=120, check=False
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}


# -- Ctrl-C ------------------------------------------------------------------------------------------------------------

SERVE_VIEW = """
import sys
from jumpstarter.common.utils import serve
from jumpstarter_driver_hmi.driver import MockHmi
from jumpstarter_driver_hmi.web import serve_view

with serve(MockHmi(width=120, height=200)) as client:
    serve_view(client, open_browser=False)
    print("serve_view returned", flush=True)
"""


def read_until(process: subprocess.Popen, text: str, timeout: float) -> str:
    """What the process printed up to the line with ``text`` (or fail after ``timeout`` seconds)."""
    assert process.stdout is not None
    output = ""
    deadline = time.monotonic() + timeout
    while text not in output:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"timed out waiting for {text!r}: {output}"
        if select.select([process.stdout], [], [], remaining)[0]:
            line = process.stdout.readline()
            assert line, f"the process ended before it printed {text!r}: {output}"
            output += line
    return output


@pytest.mark.skipif(sys.platform == "win32", reason="needs SIGINT and select on a pipe")
def test_serve_view_blocks_until_ctrl_c() -> None:
    with subprocess.Popen(
        [sys.executable, "-c", SERVE_VIEW], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as process:
        try:
            url = re.search(
                r"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+", read_until(process, "(Ctrl-C to stop)", 60)
            )
            assert url
            with urllib.request.urlopen(url.group(), timeout=30) as response:
                assert response.status == 200
            base = url.group().split("?")[0]
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"{base}info", timeout=30)
            assert refused.value.code == 403
            with urllib.request.urlopen(f"{base}info?token={token_of(url.group())}", timeout=30) as response:
                assert response.status == 200
            assert process.poll() is None  # it blocks
            process.send_signal(signal.SIGINT)
            rest = read_until(process, "serve_view returned", 30)
            assert "Stopping the screen view" in rest
            assert process.wait(timeout=30) == 0
            with pytest.raises(urllib.error.URLError):
                urllib.request.urlopen(url.group(), timeout=5)
        finally:
            process.kill()

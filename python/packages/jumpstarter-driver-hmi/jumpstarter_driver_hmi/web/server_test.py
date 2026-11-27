"""The local web view: the server (routes, the guard, the relay and its ends) and the page's own code under node.

The HMI behind the relay is a ``MockHmi`` through a real session (the real client, framing and hub), or a stub client
where a test needs the stream to do something a mock does not (be refused, block, be a fast source, say which thread
called it). The page's pure functions, and the page itself with just enough of a browser around it, run under node: what
it sends and draws for each kind of HMI (touch, mouse, view-only; H.264, HEVC and MJPEG) is checked there, and in a real
Chrome by the end-to-end check that lives outside the repository.
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
from ..common import (
    MAX_LINE_BYTES,
    MAX_TEXT_CHARS,
    Capabilities,
    CodecLine,
    HardKey,
    KeyboardCaps,
    PointerCaps,
    RotaryCaps,
)
from ..driver import MockHmi
from ..hub.codecs import NAL_IDR, NAL_PPS, NAL_SPS, nal_types
from ..testing.fake import PPS, START_CODE, FakeSource, make_sps
from ..testing.pattern import pattern_units
from . import create_app, serve_view
from .server import (
    MAX_MESSAGE_BYTES,
    SOCKETS,
    TOKEN,
    WORKERS,
    Workers,
    _serve,
    bind_address,
    run_server,
    view_url,
)
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
    """A server on a free loopback port, in front of an HMI."""

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
        """What the device behind the HMI has been given."""
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
def a_hmi(*, control: bool = True, **options: Any) -> Generator[tuple[MockHmi, HmiClient]]:
    driver = MockHmi(width=480, height=800, fps=30, gop=10, control=control, **options)
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
POINTER = {"type": "down", "kind": "touch", "x": 30, "y": 40, "id": 0, "buttons": 1, "w": 480, "h": 800}


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


async def test_info_is_the_hmis_info(running: Running) -> None:
    await to_thread.run_sync(running.client.start)
    await wait_until(lambda: running.client.hmi_info()["width"], "the picture's size")
    expected = await to_thread.run_sync(running.client.hmi_info)
    async with ClientSession() as session, session.get(running.url("/info")) as response:
        assert response.status == 200
        assert response.content_type == "application/json"
        assert await response.json() == expected
    assert expected["interface"] == "hmi"
    assert expected["input"]["pointer"]["kinds"] == ["touch"]
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


async def test_pointer_key_and_text_reach_the_device(running: Running) -> None:
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 1)
        await ws.send_json({"touch": TOUCH})  # the shorthand of a pointer of kind touch
        await ws.send_json({"touch": {**TOUCH, "type": "up"}})
        await ws.send_json({"pointer": POINTER})
        await ws.send_json({"pointer": {**POINTER, "type": "move", "x": 31, "y": 41}})
        await ws.send_json({"pointer": {**POINTER, "type": "up", "x": 31, "y": 41, "buttons": 0}})
        await ws.send_json({"key": {"key": "HOME", "press": "short"}})
        await ws.send_json({"text": "hello"})
        wanted = [
            ("touch", "down", 10, 20, (480, 800)),
            ("touch", "up", 10, 20, (480, 800)),
            ("touch", "down", 30, 40, (480, 800)),
            ("touch", "move", 31, 41, (480, 800)),
            ("touch", "up", 31, 41, (480, 800)),
            ("key", "HOME", "short"),
            ("text", "hello"),
        ]
        await wait_until(
            lambda: running.received()[: len(wanted)] == wanted, "the device to be given the events, in order"
        )
    await running.settled()


async def test_a_mouse_a_wheel_and_the_keyboard_reach_the_device() -> None:
    """The lines the page sends for a mouse HMI: each reaches the device as the exporter checked it, in order."""
    mouse = {"kind": "mouse", "id": 0, "w": 480, "h": 800}
    with a_hmi(mode="mouse") as (driver, client):
        runner, port = await run_server(client)
        try:
            async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
                await receive_media(ws, 1)
                for line in (
                    {"pointer": {**mouse, "type": "move", "x": 5, "y": 6, "buttons": 0}},  # hover
                    {"pointer": {**mouse, "type": "down", "x": 30, "y": 40, "buttons": 2}},  # a right click
                    {"pointer": {**mouse, "type": "move", "x": 31, "y": 41, "buttons": 3}},  # a second button
                    {"pointer": {**mouse, "type": "up", "x": 31, "y": 41, "buttons": 0}},
                    {"pointer": {**mouse, "type": "wheel", "x": 31, "y": 41, "dx": -5, "dy": 120}},
                    {"keyboard": {"code": "ShiftLeft", "type": "down"}},
                    {"keyboard": {"code": "KeyA", "type": "down"}},
                    {"keyboard": {"code": "KeyA", "type": "up"}},
                    {"keyboard": {"code": "ShiftLeft", "type": "up"}},
                    {"text": "typed"},
                ):
                    await ws.send_json(line)
                size = (480, 800)
                wanted = [
                    ("pointer", "move", "mouse", 5.0, 6.0, 0, 0, 0.0, 0.0, size),
                    ("pointer", "down", "mouse", 30.0, 40.0, 0, 2, 0.0, 0.0, size),
                    ("pointer", "move", "mouse", 31.0, 41.0, 0, 3, 0.0, 0.0, size),
                    ("pointer", "up", "mouse", 31.0, 41.0, 0, 0, 0.0, 0.0, size),
                    ("pointer", "wheel", "mouse", 31.0, 41.0, 0, None, -5.0, 120.0, size),
                    ("keyboard", "ShiftLeft", True),
                    ("keyboard", "KeyA", True),
                    ("keyboard", "KeyA", False),
                    ("keyboard", "ShiftLeft", False),
                    ("text", "typed"),
                ]
                sink = fake_source(driver).sink_input
                await wait_until(lambda: len(sink.snapshot()) >= len(wanted), "the device to be given the events")
                got = sink.snapshot()[: len(wanted)]
                assert got[:4] == wanted[:4]
                assert got[5:] == wanted[5:]
                assert got[4][:6] == wanted[4][:6]  # the buttons of a wheel are the device's business
                assert got[4][7:] == wanted[4][7:]
                await ws.send_json({"keyboard": {"code": "KeyB", "type": "down"}})  # still down when the page goes
                await wait_until(lambda: ("keyboard", "KeyB", True) in sink.snapshot(), "the key to be down")
            await wait_until(
                lambda: sink.snapshot()[-2:] == [("keyboard", "KeyB", False), ("release",)], "the key let go"
            )
            assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        finally:
            await runner.cleanup()


async def test_a_rotary_turn_and_its_push_reach_the_device() -> None:
    """The lines the page sends for a dial: each turn reaches the device as the exporter checked it, in order, and the
    push key is a hard key (its press is checked against the key's presses)."""
    rotaries = [{"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"}, "tune"]
    with a_hmi(rotaries=rotaries) as (driver, client):
        runner, port = await run_server(client)
        try:
            async with ClientSession() as session:
                async with session.get(url_of(runner, port, "/info")) as response:
                    declared = (await response.json())["input"]
                assert declared["rotaries"] == [
                    {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"},
                    {"id": "tune", "label": "Tune", "push": None},
                ]
                assert "VOLUME_PUSH" in declared["keys"]
                assert "VOLUME_PUSH" not in [hard["key"] for hard in declared["hard_keys"]]
                async with session.ws_connect(url_of(runner, port, "/ws")) as ws:
                    await receive_media(ws, 1)
                    for line in (
                        {"rotary": {"id": "volume", "delta": 1}},
                        {"rotary": {"id": "volume", "delta": -2}},
                        {"rotary": {"id": "tune", "delta": 100}},  # the most one line turns
                        {"rotary": {"id": "tune", "delta": -100}},
                        {"key": {"key": "VOLUME_PUSH", "press": "short"}},
                        {"key": {"key": "VOLUME_PUSH", "press": "long"}},
                        {"rotary": {"id": "volume", "delta": 3}},
                    ):
                        await ws.send_json(line)
                    wanted = [
                        ("rotary", "volume", 1),
                        ("rotary", "volume", -2),
                        ("rotary", "tune", 100),
                        ("rotary", "tune", -100),
                        ("key", "VOLUME_PUSH", "short"),
                        ("key", "VOLUME_PUSH", "long"),
                        ("rotary", "volume", 3),
                    ]
                    sink = fake_source(driver).sink_input
                    await wait_until(lambda: sink.snapshot() == wanted, "the device to be given the turns and pushes")
            assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        finally:
            await runner.cleanup()


async def test_a_rotary_line_the_hmi_does_not_take_is_answered_with_an_error() -> None:
    """The exporter says what is wrong with a turn (no such rotary, no turn, too many detents); the next one goes."""
    with a_hmi(rotaries=["volume"]) as (driver, client):
        runner, port = await run_server(client)
        try:
            async with ClientSession() as session, session.ws_connect(url_of(runner, port, "/ws")) as ws:
                await receive_media(ws, 1)
                for line, said in (
                    ({"rotary": {"id": "treble", "delta": 1}}, "no rotary treble"),
                    ({"rotary": {"id": "volume", "delta": 0}}, "not 0"),
                    ({"rotary": {"id": "volume", "delta": 101}}, "at most 100"),
                    ({"rotary": {"id": "volume", "delta": 1.5}}, "whole number"),
                ):
                    await ws.send_json(line)
                    assert said in await receive_error(ws)
                await ws.send_json({"rotary": {"id": "volume", "delta": 1}})
                sink = fake_source(driver).sink_input
                await wait_until(lambda: sink.snapshot() == [("rotary", "volume", 1)], "the turn after refusals")
            assert await to_thread.run_sync(runner.app[WORKERS].wait_idle, DEADLINE_S)
        finally:
            await runner.cleanup()


async def test_a_rotary_on_an_hmi_without_one_is_answered_with_an_error(running: Running) -> None:
    """The mock's default touch HMI has no rotary, so its /info declares none and a turn is refused."""
    async with ClientSession() as session:
        async with session.get(running.url("/info")) as response:
            assert (await response.json())["input"]["rotaries"] == []
        async with session.ws_connect(running.url("/ws")) as ws:
            await receive_media(ws, 1)
            await ws.send_json({"rotary": {"id": "volume", "delta": 1}})
            assert "no rotary volume" in await receive_error(ws)
            await ws.send_json({"key": {"key": "HOME", "press": "short"}})
            await wait_until(lambda: ("key", "HOME", "short") in running.received(), "the key after the refused turn")
    assert not any(event[0] == "rotary" for event in running.received())
    await running.settled()


async def test_a_keyboard_line_the_hmi_does_not_take_is_answered_with_an_error(running: Running) -> None:
    """The mock's keyboard types text and takes no key codes: the exporter says so, and the page shows it."""
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 1)
        await ws.send_json({"keyboard": {"code": "KeyA", "type": "down"}})
        assert "key code" in await receive_error(ws)
        await ws.send_json({"text": "still works"})
        await wait_until(lambda: ("text", "still works") in running.received(), "the text after the refused key")
    assert not any(event[0] == "keyboard" for event in running.received())
    await running.settled()


async def test_a_text_over_the_limit_is_answered_with_an_error_and_one_at_it_is_typed(running: Running) -> None:
    assert MAX_MESSAGE_BYTES == MAX_LINE_BYTES  # the relay takes what the exporter's input stream takes
    async with ClientSession() as session, session.ws_connect(running.url("/ws")) as ws:
        await receive_media(ws, 1)
        await ws.send_json({"text": "é" * (MAX_TEXT_CHARS + 1)})
        assert str(MAX_TEXT_CHARS) in await receive_error(ws)
        await ws.send_json({"text": "é" * MAX_TEXT_CHARS})
        await ws.send_json({"text": "after"})
        await wait_until(lambda: ("text", "after") in running.received(), "the text after the refused one")
    assert running.received().count(("text", "é" * MAX_TEXT_CHARS)) == 1
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
        '{"pointer": "x"}',
        '{"pointer": [1]}',
        '{"keyboard": "KeyA"}',
        '{"key": 5}',
        '{"rotary": 5}',
        '{"rotary": "volume"}',
        '{"rotary": [1]}',
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
        await ws.send_json({"key": {"key": "VOLUME_UP", "press": "short"}})
        await wait_until(lambda: ("key", "VOLUME_UP", "short") in running.received(), "a valid event after the junk")
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
                    assert (await response.json())["input"]["pointer"]["kinds"] == []
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
        return {"input": {"pointer": {"kinds": ["touch"], "absolute": True, "max_contacts": 1}}}


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
            await ws.send_json({"pointer": POINTER})
            await ws.send_json({"keyboard": {"code": "KeyA", "type": "down"}})
            await ws.send_json({"key": {"key": "HOME", "press": "long"}})
            await ws.send_json({"text": "typed"})
            await wait_until(
                lambda: len(video.calls) == 2 and len(client.input_stream.sent) == 5, "the calls to arrive"
            )
            assert [call[:2] for call in video.calls] == [("resync", None), ("lag", 0.4)]
            assert [event for event, _ in client.input_stream.sent] == [
                {"touch": TOUCH},
                {"pointer": POINTER},
                {"keyboard": {"code": "KeyA", "type": "down"}},
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

// the codec line, and the family its codec string names
const line = page.parseCodecLine(
  '{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90,"coded_width":480,"coded_height":816}',
);
assert.deepEqual(line, {
  codec: "avc1.42C01F", family: "h264", width: 480, height: 800, codedWidth: 480, codedHeight: 816,
  rotation: 90, fps: 30,
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
const families = {
  "avc1.64001F": "h264", "AVC1.42C01E": "h264", "avc3.640028": "h264", "hev1.1.6.L93.B0": "hevc",
  "hvc1.1.6.L120.90": "hevc", mjpeg: "mjpeg", MJPEG: "mjpeg", "vp09.00.10.08": null, avc1: null, "": null, 5: null,
};
for (const [codec, family] of Object.entries(families)) assert.equal(page.codecFamily(codec), family, codec);
assert.equal(page.codecFamily(undefined), null);
assert.equal(page.parseCodecLine('{"codec":"hev1.1.6.L93.B0","width":240,"height":400}').family, "hevc");
assert.equal(page.parseCodecLine('{"codec":"mjpeg","width":240,"height":400,"format":"jpeg"}').family, "mjpeg");
assert.equal(page.parseCodecLine('{"codec":"vp09.00.10.08","width":240,"height":400}').family, null);

// what restarts the decoder (a change of codec or family does, a rotation does not), how it is configured, and how
// the picture is drawn
const hevc = page.parseCodecLine('{"codec":"hev1.1.6.L93.B0","width":240,"height":400}');
const mjpeg = page.parseCodecLine('{"codec":"mjpeg","width":240,"height":400}');
assert.equal(page.restartsDecoder(null, line), true);
assert.equal(page.restartsDecoder(line, { ...line, rotation: 0, fps: 60 }), false);
assert.equal(page.restartsDecoder(line, { ...line, codec: "avc1.640028" }), true);
assert.equal(page.restartsDecoder(line, { ...line, codedHeight: 800 }), true);
assert.equal(page.restartsDecoder(hevc, mjpeg), true);
assert.equal(page.restartsDecoder(mjpeg, { ...mjpeg, fps: 5 }), false);
assert.deepEqual(page.decoderConfig(line), {
  codec: "avc1.42C01F", codedWidth: 480, codedHeight: 816, optimizeForLatency: true, avc: { format: "annexb" },
});
assert.deepEqual(page.decoderConfig(hevc), {
  codec: "hev1.1.6.L93.B0", codedWidth: 240, codedHeight: 400, optimizeForLatency: true, hevc: { format: "annexb" },
});
assert.match(page.unsupportedMessage(hevc), /cannot decode HEVC \(hev1\.1\.6\.L93\.B0\).*--codec h264/);
assert.match(page.unsupportedMessage(line), /cannot decode H\.264 \(avc1\.42C01F\)/);
assert.match(page.unsupportedMessage(mjpeg), /MJPEG/);
assert.match(page.unsupportedMessage({ codec: "vp09", family: null }), /does not know the codec vp09/);
const plan = page.canvasPlan(line);
assert.equal(plan.width, 800);
assert.equal(plan.height, 480);
assert.equal(plan.srcW, 480);
assert.equal(plan.srcH, 800);
assert.ok(Math.abs(plan.angle - Math.PI / 2) < 1e-12);

// frames: the NAL types agree with the exporter-side parser's, for both families
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
const hevcKey = hex("__HEVC_KEY__");
const hevcDelta = hex("__HEVC_DELTA__");
assert.deepEqual(page.nalTypes(hevcKey, "hevc"), __HEVC_KEY_TYPES__);
assert.deepEqual(page.nalTypes(hevcDelta, "hevc"), __HEVC_DELTA_TYPES__);
assert.equal(page.isKeyUnit(hevcKey, "hevc"), true);
assert.equal(page.isKeyUnit(hevcDelta, "hevc"), false);
assert.equal(page.isKeyUnit(new Uint8Array(0), "hevc"), false);
assert.equal(page.isKeyUnit(au, "h264"), true);
assert.equal(page.isKeyUnit(delta, "h264"), false);
// the IRAP types of HEVC are 16 to 21 (BLA, IDR, CRA); a header's second byte is not the type
const irap = [[1, false], [9, false], [15, false], [16, true], [19, true], [20, true], [21, true], [22, false]];
for (const [type, key] of irap) {
  assert.equal(page.isKeyUnit(Uint8Array.from([0, 0, 1, type << 1, 1, 0xaa]), "hevc"), key, `hevc type ${type}`);
}
// every JPEG is a key picture; a unit with no NAL units is one only for mjpeg
assert.equal(page.isKeyUnit(hex("ffd8ffe000104a46494600"), "mjpeg"), true);
assert.equal(page.isKeyUnit(hex("ffd8ffe000104a46494600"), "h264"), false);
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
  page.pointerMessage("down", "mouse", { x: 1, y: 2 }, [480, 800], 0, 1),
  { pointer: { type: "down", kind: "mouse", x: 1, y: 2, id: 0, buttons: 1, w: 480, h: 800 } },
);
assert.deepEqual(page.keyMessage("HOME", "long"), { key: { key: "HOME", press: "long" } });
// the kind: the browser's own if the HMI declares it, else the first one it declares
assert.equal(page.pointerKind("touch", ["touch"]), "touch");
assert.equal(page.pointerKind("mouse", ["touch"]), "touch");
assert.equal(page.pointerKind("touch", ["mouse"]), "mouse");
assert.equal(page.pointerKind("pen", ["touch", "mouse"]), "touch");
assert.equal(page.pointerKind("pen", ["mouse", "pen"]), "pen");
assert.equal(page.pointerKind("", ["mouse"]), "mouse");
// the buttons: after the event; an HMI without buttons has the primary one only, and a press has at least that
assert.equal(page.pointerButtons("down", 1, { buttons: true }), 1);
assert.equal(page.pointerButtons("down", 2, { buttons: true }), 2);
assert.equal(page.pointerButtons("down", 0, { buttons: true }), 1);
assert.equal(page.pointerButtons("down", undefined, { buttons: false }), 1);
assert.equal(page.pointerButtons("down", 2, { buttons: false }), 1);
assert.equal(page.pointerButtons("move", 3, { buttons: true }), 3);
assert.equal(page.pointerButtons("move", 3, { buttons: false }), 1);
assert.equal(page.pointerButtons("move", 0, { buttons: true }), 0);
assert.equal(page.pointerButtons("move", 2, { buttons: false }), 0);
assert.equal(page.pointerButtons("up", 1, { buttons: true }), 0);
assert.equal(page.pointerButtons("cancel", 1, { buttons: true }), 0);
assert.equal(page.pointerButtons("move", 99, { buttons: true }), 35); // masked to the bits there are
// what an event of the browser's pointer is to the HMI: a contact is down while a declared button is, whatever else
// is held (the browser reports a second button as a move whose buttons changed, never as a pointerdown or a pointerup)
const primary = { buttons: false };
const everyButton = { buttons: true };
assert.equal(page.pointerStep("pointerdown", 1, false, primary), "down");
assert.equal(page.pointerStep("pointerdown", 2, false, primary), null); // the secondary button is not declared
assert.equal(page.pointerStep("pointermove", 3, false, primary), "down"); // the primary joins the secondary: a move
assert.equal(page.pointerStep("pointermove", 3, true, primary), "move");
assert.equal(page.pointerStep("pointermove", 1, true, primary), "move");
assert.equal(page.pointerStep("pointermove", 2, true, primary), "up"); // the primary is let go, the secondary held
assert.equal(page.pointerStep("pointermove", 0, true, primary), "up");
assert.equal(page.pointerStep("pointermove", 2, false, primary), "hover"); // an undeclared button does not stop a hover
assert.equal(page.pointerStep("pointermove", 0, false, primary), "hover");
assert.equal(page.pointerStep("pointerup", 0, true, primary), "up");
assert.equal(page.pointerStep("pointerup", 1, true, primary), "up"); // the last button went up, whatever the event says
assert.equal(page.pointerStep("pointerup", 0, false, primary), null);
assert.equal(page.pointerStep("pointerup", 2, false, primary), null);
assert.equal(page.pointerStep("pointerdown", undefined, false, primary), "down"); // no buttons given: the primary one
assert.equal(page.pointerStep("pointermove", undefined, false, primary), "hover");
assert.equal(page.pointerStep("pointermove", undefined, true, primary), "up");
// an HMI that declares buttons follows them all: the first one down begins the contact, the last one up ends it
assert.equal(page.pointerStep("pointerdown", 2, false, everyButton), "down");
assert.equal(page.pointerStep("pointermove", 3, true, everyButton), "move");
assert.equal(page.pointerStep("pointermove", 2, true, everyButton), "move");
assert.equal(page.pointerStep("pointermove", 0, true, everyButton), "up");
assert.equal(page.pointerStep("pointermove", 3, false, everyButton), "down");
assert.equal(page.pointerStep("pointermove", 64, false, everyButton), "hover"); // a bit that is no button
// the wheel: pixels with the sign of deltaX and deltaY; lines and pages are turned into pixels
assert.deepEqual(page.wheelDeltas({ deltaMode: 0, deltaX: 3, deltaY: -120 }, [480, 800]), { dx: 3, dy: -120 });
assert.deepEqual(page.wheelDeltas({ deltaMode: 1, deltaX: 0, deltaY: 3 }, [480, 800]), { dx: 0, dy: 3 * page.LINE_PX });
assert.deepEqual(page.wheelDeltas({ deltaMode: 2, deltaX: 1, deltaY: -1 }, [480, 800]), { dx: 480, dy: -800 });
assert.deepEqual(page.wheelDeltas({ deltaMode: 0 }, [480, 800]), { dx: 0, dy: 0 });
assert.deepEqual(
  page.wheelMessage("mouse", { x: 5, y: 6 }, [480, 800], { dx: 0, dy: 120 }),
  { pointer: { type: "wheel", kind: "mouse", x: 5, y: 6, id: 0, dx: 0, dy: 120, w: 480, h: 800 } },
);
// the contacts: the lowest free one, none when the HMI follows no more fingers
const contacts = new page.Contacts(2);
assert.equal(contacts.acquire(7), 0);
assert.equal(contacts.acquire(7), 0);
assert.equal(contacts.acquire(9), 1);
assert.equal(contacts.acquire(11), -1);
assert.equal(contacts.size, 2);
contacts.release(7);
assert.equal(contacts.acquire(11), 0);
assert.equal(contacts.has(7), false);
contacts.clear();
assert.equal(contacts.size, 0);
assert.equal(new page.Contacts(1).acquire(3), 0);

// what /info says the HMI takes: the page offers that and nothing more
const touchHmi = page.inputPlan({
  input: {
    pointer: { kinds: ["touch"], absolute: true, relative: false, buttons: false, wheel: false, max_contacts: 1 },
    keyboard: { codes: [], text: true, layout: "us" },
    keys: ["home", "BACK"],
    hard_keys: [{ key: "HOME", label: "Home", presses: ["short", "long"] }],
    clipboard: false,
  },
});
assert.deepEqual(touchHmi.pointer, {
  kinds: ["touch"], absolute: true, relative: false, buttons: false, wheel: false, maxContacts: 1,
});
assert.deepEqual(touchHmi.keyboard, { codes: [], text: true, layout: "us" });
assert.deepEqual(touchHmi.keys, ["HOME", "BACK"]);
assert.equal(touchHmi.hardKeys.length, 1);
assert.equal(touchHmi.viewOnly, false);
assert.equal(touchHmi.relativeOnly, false);
const mouseHmi = page.inputPlan({
  input: {
    pointer: {
      kinds: ["mouse", "pen", "bogus"], absolute: true, relative: true, buttons: true, wheel: true, max_contacts: 3,
    },
    keyboard: { codes: "all", text: false, layout: "de" },
    keys: [],
    hard_keys: [],
  },
});
assert.deepEqual(mouseHmi.pointer, {
  kinds: ["mouse", "pen"], absolute: true, relative: true, buttons: true, wheel: true, maxContacts: 3,
});
assert.deepEqual(mouseHmi.keyboard, { codes: "all", text: false, layout: "de" });
assert.equal(mouseHmi.viewOnly, false);
const listed = page.inputPlan({ input: { keyboard: { codes: ["KeyA", "no good", "Enter"], text: false } } });
assert.deepEqual(listed.keyboard, { codes: ["KeyA", "Enter"], text: false, layout: "us" });
assert.equal(listed.pointer, null);
// a HMI that declares nothing, or says nothing sensible, is view only
for (const info of [
  { input: { pointer: { kinds: [] }, keyboard: { codes: [], text: false }, keys: [], hard_keys: [] } },
  { input: {} },
  {},
  null,
  undefined,
  "text",
  { input: "none" },
  { input: { pointer: { kinds: ["bogus"] }, keyboard: { codes: ["no good"] }, hard_keys: [{ label: "no key" }] } },
  { input: { rotaries: [] } },
  { input: { rotaries: "volume" } },
  { input: { rotaries: [null, 5, { label: "no id" }, { id: "1st" }, { id: "has space" }, { id: "x".repeat(33) }] } },
]) {
  const view = page.inputPlan(info);
  assert.equal(view.viewOnly, true, JSON.stringify(info));
  assert.equal(view.pointer, null);
  assert.equal(view.keyboard, null);
  assert.deepEqual(view.hardKeys, []);
  assert.deepEqual(view.rotaries, []); // no dial for an HMI that declares none
}
assert.deepEqual(touchHmi.rotaries, []);
assert.deepEqual(mouseHmi.rotaries, []);
// hard keys alone are input; a pointer with no positions is said to be relative only
assert.equal(page.inputPlan({ input: { hard_keys: [{ key: "HOME" }] } }).viewOnly, false);
const relative = page.inputPlan({
  input: { pointer: { kinds: ["mouse"], absolute: false, relative: true, max_contacts: 1 } },
});
assert.equal(relative.relativeOnly, true);
assert.equal(relative.viewOnly, false);

// rotaries: a dial for each declared one (a rotary alone is input), with a push button when its key is one the HMI
// takes, and the push key is not also a hard key button
const rotaryInfo = {
  input: {
    keys: ["VOLUME_UP", "VOLUME_PUSH", "TUNE_PUSH", "SEEK_PUSH", "MUTE", "HOME"],
    hard_keys: [
      { key: "VOLUME_UP", label: "Volume Up", presses: ["short", "long"] },
      { key: "VOLUME_PUSH", label: "Volume Push", presses: ["short"] },
      { key: "TUNE_PUSH", label: "Tune Push" },
      { key: "SEEK_PUSH", label: "Seek Push", presses: ["down", "up"] },
      { key: "HOME", label: "Home", presses: ["short", "long"] },
    ],
    rotaries: [
      { id: "volume", label: "Volume", push: "VOLUME_PUSH" },
      { id: "tune-knob", label: "  ", push: "tune_push" },
      { id: "seek", label: "Seek", push: "SEEK_PUSH" },
      { id: "mute_dial", label: "Mute dial", push: "MUTE" },
      { id: "bare", label: "Bare", push: null },
      { id: "volume", label: "Volume again", push: null },
      { id: "far", label: "Far", push: "NOT_A_KEY" },
      { id: "no-label", push: 5 },
      { id: "1bad", label: "Bad id", push: null },
      null,
    ],
  },
};
const rotaryPlan = page.inputPlan(rotaryInfo);
assert.equal(rotaryPlan.viewOnly, false);
assert.deepEqual(rotaryPlan.rotaries, [
  { id: "volume", label: "Volume", push: { key: "VOLUME_PUSH", label: "Push", short: true, long: false } },
  { id: "tune-knob", label: "tune-knob", push: { key: "TUNE_PUSH", label: "Push", short: true, long: true } },
  { id: "seek", label: "Seek", push: null }, // a key that takes neither a short nor a long press has no button
  { id: "mute_dial", label: "Mute dial", push: { key: "MUTE", label: "Push", short: true, long: true } }, // no hard key
  { id: "bare", label: "Bare", push: null },
  { id: "far", label: "Far", push: null }, // a key the HMI does not take is no push button
  { id: "no-label", label: "no-label", push: null },
]);
// the push keys are drawn with their dial and not again: only the other hard keys are buttons
assert.deepEqual(rotaryPlan.hardKeys.map((b) => b.key), ["VOLUME_UP", "HOME"]);
assert.deepEqual(rotaryPlan.keys, ["VOLUME_UP", "VOLUME_PUSH", "TUNE_PUSH", "SEEK_PUSH", "MUTE", "HOME"]);
// a rotary alone is input; with no hard keys at all there is only the dial
const dialOnly = page.inputPlan({ input: { rotaries: [{ id: "volume", label: "Volume", push: null }] } });
assert.equal(dialOnly.viewOnly, false);
assert.deepEqual(dialOnly.hardKeys, []);
assert.equal(dialOnly.rotaries.length, 1);
// a hard key that no rotary names stays a button, and so does one that merely looks like a push key
const pushLike = page.inputPlan({
  input: {
    keys: ["VOLUME_PUSH"],
    hard_keys: [{ key: "VOLUME_PUSH", label: "Volume Push" }],
    rotaries: [{ id: "tune", label: "Tune", push: null }],
  },
});
assert.deepEqual(pushLike.hardKeys.map((b) => b.key), ["VOLUME_PUSH"]);
assert.deepEqual(page.rotaryDials(undefined, [], []), []);
assert.deepEqual(page.rotaryDials([{ id: "a", label: "A", push: "UP" }], ["UP"], undefined), [
  { id: "a", label: "A", push: { key: "UP", label: "Push", short: true, long: true }, pushKey: "UP" },
]);

// a turn is one line of whole detents, clockwise positive; more than the exporter takes in one line is several
assert.deepEqual(page.rotaryMessages("volume", 3), [{ rotary: { id: "volume", delta: 3 } }]);
assert.deepEqual(page.rotaryMessages("volume", -1), [{ rotary: { id: "volume", delta: -1 } }]);
assert.deepEqual(page.rotaryMessages("volume", 0), []);
assert.deepEqual(page.rotaryMessages("volume", NaN), []);
assert.deepEqual(page.rotaryMessages("volume", Infinity), []);
assert.deepEqual(page.rotaryMessages("volume", 2.9), [{ rotary: { id: "volume", delta: 2 } }]);
assert.equal(page.MAX_ROTARY_DELTA, 100);
assert.deepEqual(page.rotaryMessages("v", 100).map((m) => m.rotary.delta), [100]);
assert.deepEqual(page.rotaryMessages("v", 101).map((m) => m.rotary.delta), [100, 1]);
assert.deepEqual(page.rotaryMessages("v", -250).map((m) => m.rotary.delta), [-100, -100, -50]);
// the wheel: whole detents of 100 pixels, the rest kept, the sign kept, never -0
assert.equal(page.WHEEL_DETENT_PX, 100);
const detents = new page.Detents();
assert.equal(detents.take(30), 0);
assert.equal(detents.rest, 30);
assert.equal(detents.take(70), 1);
assert.equal(detents.rest, 0);
assert.equal(detents.take(250), 2);
assert.equal(detents.rest, 50);
assert.equal(detents.take(-50), 0);
assert.ok(Object.is(detents.take(-30), 0)); // not -0
assert.equal(detents.take(-80), -1);
assert.equal(detents.rest, -10);
assert.equal(detents.take(NaN), 0);
assert.equal(detents.take(Infinity), 0);
assert.equal(detents.rest, -10);
assert.equal(detents.take(-9990), -100); // as many as the pixels make; the lines are cut when they are sent
detents.take(55);
detents.clear();
assert.equal(detents.rest, 0);
assert.equal(new page.Detents(10).take(35), 3);
// a wheel over a dial: scrolling up (a negative deltaY) is clockwise; lines and pages become pixels as for a pointer
const wheelled = new page.Detents();
const turned = (init, shown = [480, 800]) =>
  page.wheelTurn(wheelled, { deltaMode: 0, deltaX: 0, deltaY: 0, ...init }, shown);
assert.equal(turned({ deltaY: -100 }), 1);
assert.equal(turned({ deltaY: 100 }), -1);
assert.equal(turned({ deltaY: -40 }), 0);
assert.equal(turned({ deltaY: -60 }), 1);
assert.equal(turned({ deltaX: 500 }), 0); // only the vertical delta turns a dial
assert.equal(turned({ deltaX: -500, deltaY: -100 }), 1);
assert.equal(wheelled.rest, 0);
assert.equal(turned({ deltaMode: 1, deltaY: -3 }), 1); // three lines of 40 pixels
assert.equal(wheelled.rest, 20);
assert.equal(turned({ deltaMode: 2, deltaY: 1 }), -7); // a page is the shown picture's height: 800 pixels
assert.equal(wheelled.rest, -80);
assert.equal(turned({ deltaMode: 2, deltaY: -1 }, [200, 200]), 1);
assert.equal(wheelled.rest, 20);
assert.equal(turned({}), 0);
// the arrow keys: right and up are clockwise, left and down are not; a shortcut is the browser's
const arrow = (key, init = {}) =>
  page.arrowTurn({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init });
assert.deepEqual(["ArrowRight", "ArrowUp", "ArrowLeft", "ArrowDown"].map((key) => arrow(key)), [1, 1, -1, -1]);
const notArrows = ["KeyA", "Enter", "Home", "PageUp", "constructor", "__proto__", undefined, 5];
assert.deepEqual(notArrows.map((key) => arrow(key)), notArrows.map(() => 0));
for (const held of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { isComposing: 1 }]) {
  assert.equal(arrow("ArrowRight", held), 0);
}

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

// the keyboard on the focused picture
const ev = (init) => ({
  key: "", code: "", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, isComposing: false, ...init,
});
const code = (c, key = "") => ({ kind: "code", code: c, message: { keyboard: { code: c, type: "down" } } });
const typedText = (t) => ({ kind: "text", message: { text: t } });
const named = (name) => ({ kind: "key", message: { key: { key: name, press: "short" } } });
const everything = { codes: "all", text: true, layout: "us" };
const someCodes = { codes: ["KeyA", "Enter"], text: true, layout: "us" };
const textOnly = { codes: [], text: true, layout: "us" };
const codesOnly = { codes: ["KeyA"], text: false, layout: "us" };
const names = ["ENTER", "DEL", "ESCAPE", "TAB"];
// a key the HMI takes is its code, whatever the character: never text
assert.deepEqual(page.keyboardAction(ev({ code: "KeyA", key: "a" }), everything, names), code("KeyA"));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyA", key: "A", shiftKey: true }), everything, names), code("KeyA"));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyQ", key: "a" }), everything, names), code("KeyQ")); // AZERTY
assert.deepEqual(
  page.keyboardAction(ev({ code: "ArrowLeft", key: "ArrowLeft" }), everything, names), code("ArrowLeft"),
);
assert.deepEqual(page.keyboardAction(ev({ code: "Space", key: " " }), everything, names), code("Space"));
assert.deepEqual(page.keyboardAction(ev({ code: "Enter", key: "Enter" }), everything, names), code("Enter"));
const shift = ev({ code: "ShiftLeft", key: "Shift", shiftKey: true });
assert.deepEqual(page.keyboardAction(shift, everything, names), code("ShiftLeft"));
const control = ev({ code: "ControlLeft", key: "Control", ctrlKey: true });
assert.deepEqual(page.keyboardAction(control, everything, names), code("ControlLeft"));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyA", key: "a" }), someCodes, names), code("KeyA"));
// a list that lacks the code: typed as text when the key is one printable character, else nothing
assert.deepEqual(page.keyboardAction(ev({ code: "KeyB", key: "b" }), someCodes, names), typedText("b"));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyB", key: "B", shiftKey: true }), someCodes, names), typedText("B"));
const percent = ev({ code: "Digit5", key: "%", shiftKey: true });
assert.deepEqual(page.keyboardAction(percent, someCodes, names), typedText("%"));
assert.equal(page.keyboardAction(ev({ code: "KeyB", key: "b" }), codesOnly, names), null);
assert.equal(page.keyboardAction(ev({ code: "ArrowUp", key: "ArrowUp" }), someCodes, names), null);
assert.equal(page.keyboardAction(ev({ code: "F5", key: "F5" }), someCodes, names), null);
assert.equal(page.keyboardAction(ev({ code: "Dead", key: "Dead" }), someCodes, names), null);
// text only: printable characters are text, and the named keys the HMI declares are its own
assert.deepEqual(page.keyboardAction(ev({ code: "KeyX", key: "x" }), textOnly, names), typedText("x"));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyX", key: "X", shiftKey: true }), textOnly, names), typedText("X"));
assert.deepEqual(page.keyboardAction(ev({ code: "Space", key: " " }), textOnly, names), typedText(" "));
assert.deepEqual(page.keyboardAction(ev({ code: "KeyE", key: "é" }), textOnly, names), typedText("é"));
assert.deepEqual(page.keyboardAction(ev({ code: "Enter", key: "Enter" }), textOnly, names), named("ENTER"));
assert.deepEqual(page.keyboardAction(ev({ code: "Backspace", key: "Backspace" }), textOnly, names), named("DEL"));
assert.deepEqual(page.keyboardAction(ev({ code: "Escape", key: "Escape" }), textOnly, names), named("ESCAPE"));
assert.deepEqual(page.keyboardAction(ev({ code: "Tab", key: "Tab" }), textOnly, names), named("TAB"));
assert.equal(page.keyboardAction(ev({ code: "Enter", key: "Enter" }), textOnly, []), null); // not declared
assert.equal(page.keyboardAction(ev({ code: "Enter", key: "Enter" }), textOnly, ["TAB"]), null);
assert.equal(page.keyboardAction(ev({ code: "ArrowUp", key: "ArrowUp" }), textOnly, names), null);
const others = [
  "Shift", "Control", "F1", "Dead", "Unidentified", "Process", "Delete", "Home", "constructor", "__proto__",
];
for (const key of others) {
  assert.equal(page.keyboardAction(ev({ code: "Nope", key }), textOnly, names), null, key);
}
// Shift+Tab is the browser's, so that focus can always leave; Tab itself is the HMI's when it takes it
assert.equal(page.keyboardAction(ev({ code: "Tab", key: "Tab", shiftKey: true }), everything, names), null);
assert.deepEqual(page.keyboardAction(ev({ code: "Tab", key: "Tab" }), everything, names), code("Tab"));
// a shortcut (Ctrl, Meta or Alt with another key) is the browser's, and so is an IME's key
for (const held of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
  assert.equal(page.keyboardAction(ev({ code: "KeyR", key: "r", ...held }), everything, names), null);
  assert.equal(page.keyboardAction(ev({ code: "KeyR", key: "r", ...held }), textOnly, names), null);
  assert.equal(page.keyboardAction(ev({ code: "Enter", key: "Enter", ...held }), textOnly, names), null);
}
assert.equal(page.keyboardAction(ev({ code: "KeyA", key: "a", isComposing: true }), everything, names), null);
assert.equal(page.keyboardAction(ev({ code: "Process", key: "Process", isComposing: true }), everything, names), null);
// except AltGr, which is how some layouts type a character
const altGr = (init) => ev({ ctrlKey: true, altKey: true, getModifierState: (name) => name === "AltGraph", ...init });
assert.deepEqual(page.keyboardAction(altGr({ code: "KeyQ", key: "@" }), textOnly, names), typedText("@"));
assert.equal(page.keyboardAction(ev({ code: "KeyA", key: "a" }), null, names), null); // no keyboard declared
assert.equal(page.keyboardAction({ ctrlKey: false }, everything, names), null);
assert.deepEqual(page.keyUpMessage("KeyA"), { keyboard: { code: "KeyA", type: "up" } });
assert.equal(page.codeTaken(everything, "KeyA"), true);
assert.equal(page.codeTaken(everything, "Key A"), false);
assert.equal(page.codeTaken(everything, 5), false);
assert.equal(page.codeTaken(someCodes, "KeyB"), false);
assert.equal(page.codeTaken(null, "KeyA"), false);

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
    hevc_units = pattern_units("hevc")  # a real key picture (VPS, SPS, PPS, IDR) and a delta one
    values = {
        "__MODULE__": module.as_uri(),
        "__ACCESS_UNIT_TYPES__": json.dumps(nal_types(access_unit)),
        "__DELTA_TYPES__": json.dumps(nal_types(delta)),
        "__WITH_SEI_TYPES__": json.dumps(nal_types(with_sei)),
        "__ACCESS_UNIT__": access_unit.hex(),
        "__DELTA__": delta.hex(),
        "__WITH_SEI__": with_sei.hex(),
        "__MEDIA__": media.hex(),
        "__HEVC_KEY_TYPES__": json.dumps(nal_types(hevc_units[0], "hevc")),
        "__HEVC_DELTA_TYPES__": json.dumps(nal_types(hevc_units[1], "hevc")),
        "__HEVC_KEY__": hevc_units[0].hex(),
        "__HEVC_DELTA__": hevc_units[1].hex(),
        "__PICTURES__": json.dumps(pictures),
        "__LAGS__": json.dumps(lags),
    }
    script = PURE_FUNCTIONS
    for token in sorted(values, key=len, reverse=True):  # the longer names first: one is the start of another
        script = script.replace(token, values[token])
    assert "__" not in re.sub(r"__proto__", "", script), "a placeholder was left"
    result = subprocess.run(
        [node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}


#: The page itself, run under node with just enough of a browser around it (a document, a socket, a decoder, a picture
#: decoder), to see what it sends and draws for what it is given. ``__INFO__`` is what ``/info`` answers. The browser's
#: own behavior (pointer capture, the real decoders) is not in it: the end-to-end check in a real Chrome covers that.
PAGE_PRELUDE = r"""
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
    this.tabIndex = 0;
    this.rect = { left: 0, top: 0, width: 400, height: 240 };
    this.classes = new Set();
    this.classList = { add: (c) => this.classes.add(c) };
    this.calls = [];
    this.captured = null;
    this.className = "";
    this.attributes = {};
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, handler, options = {}) {
    const entry = { handler, options };
    (this.listeners[type] ||= []).push(entry);
    if (options.signal) {
      options.signal.addEventListener("abort", () => {
        this.listeners[type] = this.listeners[type].filter((e) => e !== entry);
      });
    }
  }
  listenerCount() {
    return Object.values(this.listeners).reduce((n, list) => n + list.length, 0);
  }
  fire(type, event = {}) {
    const fired = { prevented: false, preventDefault() { this.prevented = true; }, ...event };
    for (const { handler } of [...(this.listeners[type] || [])]) handler(fired);
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
const ids = [
  "hmi", "status", "note", "hint", "errors", "keys", "dials", "controls", "text-form", "text-input", "reconnect",
];
for (const id of ids) {
  elements[id] = new FakeElement(id);
}
for (const id of ["note", "hint", "dials", "controls", "text-form", "reconnect"]) elements[id].hidden = true;
elements.hmi.hidden = true;
const documentListeners = new FakeElement("document");
globalThis.document = {
  getElementById: (id) => elements[id],
  createElement: (tag) => new FakeElement(tag),
  addEventListener: (type, handler, options) => documentListeners.addEventListener(type, handler, options),
  activeElement: null,
  hidden: false,
};
globalThis.window = new FakeElement("window");
globalThis.location = { protocol: "http:", host: "127.0.0.1:4321", search: "__SEARCH__" };
globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);

// -- a socket, the decoders, and /info ------------------------------------------------------------
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
const checked = []; // the configurations the browser was asked about
globalThis.VideoDecoder = class {
  static supports = () => true; // a test says what this browser cannot decode
  static async isConfigSupported(config) { checked.push(config); return { supported: VideoDecoder.supports(config) }; }
  constructor(init) {
    this.init = init; this.state = "unconfigured"; this.decodeQueueSize = 0; this.configs = []; this.chunks = [];
    decoders.push(this);
  }
  configure(config) { this.state = "configured"; this.configs.push(config); }
  reset() { this.state = "unconfigured"; }
  close() { this.state = "closed"; this.wasClosed = true; }
  decode(chunk) {
    this.chunks.push(chunk);
    this.init.output({ displayWidth: 480, displayHeight: 800, close() { this.closed = true; } });
  }
};
globalThis.EncodedVideoChunk = class { constructor(init) { Object.assign(this, init); } };
// pictures: a unit of n bytes gives a bitmap of n bytes (its `size`), made at once or when the test says
const bitmaps = []; // every one that was asked for: {size, type, resolve(), reject(error), bitmap}
let holdBitmaps = false;
globalThis.createImageBitmap = (blob) => new Promise((resolve, reject) => {
  const entry = {
    size: blob.size, type: blob.type, bitmap: null,
    resolve: () => { entry.bitmap = { size: blob.size, close() { this.closed = true; } }; resolve(entry.bitmap); },
    reject,
  };
  bitmaps.push(entry);
  if (!holdBitmaps) entry.resolve();
});
let info = __INFO__;
const fetched = [];
globalThis.fetch = async (url) => { // /info takes a moment: what the page offers before it answers can be seen
  fetched.push(url);
  await sleep(5);
  return { ok: true, json: async () => info };
};

// -- frames ---------------------------------------------------------------------------------------
const start = [0, 0, 0, 1];
const keyUnit = Uint8Array.from([...start, 0x67, 1, 2, ...start, 0x68, 3, ...start, 0x65, 4, 5, 6]);
const deltaUnit = Uint8Array.from([...start, 0x41, 9, 9, 9]);
const hevcKeyUnit = Uint8Array.from(
  [...start, 0x40, 1, 7, ...start, 0x42, 1, 8, ...start, 0x44, 1, 9, ...start, 0x26, 1, 5, 5], // VPS, SPS, PPS, IDR
);
const hevcDeltaUnit = Uint8Array.from([...start, 0x02, 1, 9, 9]);
const jpegUnit = (n) => Uint8Array.from([0xff, 0xd8, ...new Array(n).fill(7), 0xff, 0xd9]); // 4 + n bytes
function media(ptsUs, unit) {
  const buffer = new ArrayBuffer(8 + unit.length);
  const view = new DataView(buffer);
  view.setUint32(0, Math.floor(ptsUs / 4294967296));
  view.setUint32(4, ptsUs % 4294967296);
  new Uint8Array(buffer, 8).set(unit);
  return buffer;
}
const LINE = '{"codec":"avc1.42C01F","width":240,"height":400,"fps":15}';
const HEVC_LINE = '{"codec":"hev1.1.6.L93.B0","width":240,"height":400,"fps":15,"format":"annexb"}';
const MJPEG_LINE = '{"codec":"mjpeg","width":240,"height":400,"fps":15,"format":"jpeg"}';
const canvas = elements.hmi;
const pointer = (extra) => ({
  isPrimary: true, pointerType: "mouse", button: 0, buttons: 1, pointerId: 1, clientX: 0, clientY: 0, ...extra,
});
const typed = (init) => canvas.fire("keydown", {
  key: "", code: "", repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init,
});
const released = (init) => canvas.fire("keyup", { key: "", code: "", ...init });
// the page, opened on a socket that is open and has said what it carries (a 240 x 400 picture shown at half size)
async function openPage(line = LINE) {
  await import("__MODULE__");
  const ws = sockets[0];
  ws.open();
  ws.receive(line);
  canvas.rect = { left: 0, top: 0, width: 120, height: 200 };
  await sleep(20); // the browser is asked about the codec, and /info is read
  return ws;
}
"""

#: The page of a touch HMI that takes text and a few named keys, over H.264: the token, the decoder, touch, hard keys,
#: text, errors, lag and reconnecting.
PAGE_TOUCH = r"""
await import("__MODULE__");
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
// nothing is offered, and nothing is said about input, until /info has answered: no listener, no note
assert.equal(canvas.listenerCount(), 0);
assert.equal(elements.note.hidden, true);

// the codec line sizes the canvas (turned 90 degrees: 800 x 480); the browser is asked whether it can decode it; and
// the pictures that arrive meanwhile are kept from the key picture on, so that the first one is not lost to the wait
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90}');
assert.equal(canvas.width, 800);
assert.equal(canvas.height, 480);
assert.equal(canvas.hidden, false);
ws.receive(media(1_700_000_000_000_000, deltaUnit)); // a delta picture before the first key picture is dropped
ws.receive(media(1_700_000_000_033_000, keyUnit));
ws.receive(media(1_700_000_000_066_000, deltaUnit));
assert.equal(decoders.length, 0);
await sleep(20);
const config = {
  codec: "avc1.42C01F", codedWidth: 480, codedHeight: 800, optimizeForLatency: true, avc: { format: "annexb" },
};
assert.deepEqual(checked, [config]);
assert.equal(decoders.length, 1);
assert.deepEqual(decoders[0].configs, [config]);
assert.deepEqual(decoders[0].chunks.map((c) => c.type), ["key", "delta"]);
assert.equal(decoders[0].chunks[0].timestamp, 1_700_000_000_033_000);
assert.match(elements.status.textContent, /Live: 800×480, turned 90°.*avc1\.42C01F/);

// the picture is drawn turned about its middle
const drawn = canvas.calls.filter(([name]) => name === "drawImage");
assert.equal(drawn.length, 2);
assert.deepEqual(canvas.calls.find(([name]) => name === "translate"), ["translate", 400, 240]);
assert.ok(Math.abs(canvas.calls.find(([name]) => name === "rotate")[1] - Math.PI / 2) < 1e-12);
assert.deepEqual(drawn[0].slice(2), [-240, -400, 480, 800]);

// what /info says is offered: the hard keys as buttons, and the text box
assert.ok(fetched.length >= 1 && fetched.every((url) => url === "/info?token=abc-DEF_12"), JSON.stringify(fetched));
assert.equal(elements.controls.hidden, false);
assert.equal(elements["text-form"].hidden, false);
assert.equal(elements.note.hidden, true);
assert.equal(elements.hint.hidden, false); // there is a keyboard: it says how to use it
assert.match(elements.hint.textContent, /Click the picture/);
assert.equal(canvas.tabIndex, 0);
assert.equal(canvas.style.cursor, "crosshair");
assert.deepEqual(elements.keys.children.map((b) => b.textContent), ["Home", "Power"]);
assert.equal(elements.keys.hidden, false);
assert.equal(elements.dials.hidden, true); // no rotary is declared: no dial
assert.deepEqual(elements.dials.children, []);
const [home, power] = elements.keys.children;
// it listens for a pointer and for keys, and for no wheel (the HMI has none): nothing the HMI does not declare
assert.deepEqual(Object.keys(canvas.listeners).filter((t) => canvas.listeners[t].length).sort(), [
  "blur", "contextmenu", "keydown", "keyup", "lostpointercapture",
  "pointercancel", "pointerdown", "pointermove", "pointerup",
]);

// the pointer is one finger; the picture is shown at 400 x 240 for 800 x 480: a point is twice the pixel
canvas.rect = { left: 0, top: 0, width: 400, height: 240 };
const touch = (type, x, y, buttons = 1) => ({ pointer: { type, kind: "touch", x, y, id: 0, buttons, w: 800, h: 480 } });
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 200, clientY: 120 })); // a mouse on a touch-only HMI is a touch
// a second contact: the HMI follows no more fingers
canvas.fire("pointerdown", pointer({ pointerId: 2, isPrimary: false, clientX: 1, clientY: 1 }));
canvas.fire("pointermove", pointer({ pointerId: 2, isPrimary: false, clientX: 2, clientY: 2 }));
canvas.fire("pointermove", pointer({ clientX: 210, clientY: 120 }));
canvas.fire("pointermove", pointer({ clientX: 220, clientY: 121 }));
canvas.fire("pointermove", pointer({ clientX: 230, clientY: 122 }));
await sleep(30); // one animation frame: the three moves are one
canvas.fire("pointerup", pointer({ clientX: 240, clientY: 123, buttons: 0 }));
assert.deepEqual(ws.sent, [touch("down", 400, 240), touch("move", 460, 244), touch("up", 480, 246, 0)]);
assert.equal(canvas.captured, 1);
canvas.fire("lostpointercapture", pointer({})); // after the up: nothing is down, so nothing is cancelled
// not the primary button: a touch has none
canvas.fire("pointerdown", pointer({ button: 2, buttons: 2, clientX: 5, clientY: 5 }));
canvas.fire("pointermove", pointer({ buttons: 0, clientX: 50, clientY: 50 })); // no hover for a finger
await sleep(30);
assert.equal(ws.sent.length, 3);
canvas.fire("pointerdown", pointer({ clientX: 100, clientY: 60 }));
canvas.fire("lostpointercapture", pointer({}));
assert.deepEqual(ws.sent.slice(3), [touch("down", 200, 120), touch("cancel", 200, 120, 0)]);
canvas.fire("pointerdown", pointer({ clientX: 100, clientY: 60 })); // and the page losing focus lets go of it
window.fire("blur");
assert.deepEqual(ws.sent.slice(5), [touch("down", 200, 120), touch("cancel", 200, 120, 0)]);

// buttons beyond the primary one are not the HMI's, and a browser reports a second button as a move: the contact
// follows the primary button alone, whatever order the buttons go down and up in
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ button: 2, buttons: 2, clientX: 20, clientY: 10 })); // the right button: nothing
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 40, clientY: 20 })); // the left one joins it: a touch begins
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 60, clientY: 30 })); // the left is let go, the right held
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 80, clientY: 40 })); // and pressed again
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 100, clientY: 50 })); // and let go again
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 110, clientY: 55 })); // the right alone: no finger hover
canvas.fire("pointerup", pointer({ button: 2, buttons: 0, clientX: 120, clientY: 60 })); // the right is let go: nothing
await sleep(30);
assert.deepEqual(ws.sent, [
  touch("down", 80, 40), touch("up", 120, 60, 0), touch("down", 160, 80), touch("up", 200, 100, 0),
]);
// the contact is free again: a plain click goes through
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 10, clientY: 10 }));
canvas.fire("pointerup", pointer({ clientX: 10, clientY: 10, buttons: 0 }));
assert.deepEqual(ws.sent, [touch("down", 20, 20), touch("up", 20, 20, 0)]);

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

// typing on the focused picture: a character is text (this HMI takes no key codes), Enter and Backspace are keys it
// declares; the rest is the browser's
ws.sent.length = 0;
assert.equal(typed({ key: "a", code: "KeyA" }).prevented, true);
assert.equal(typed({ key: "A", code: "KeyA", shiftKey: true }).prevented, true);
assert.equal(typed({ key: " ", code: "Space" }).prevented, true);
assert.equal(typed({ key: "é", code: "KeyE" }).prevented, true);
assert.equal(typed({ key: "Enter", code: "Enter" }).prevented, true);
assert.equal(typed({ key: "Backspace", code: "Backspace" }).prevented, true);
assert.deepEqual(ws.sent, [
  { text: "a" },
  { text: "A" },
  { text: " " },
  { text: "é" },
  { key: { key: "ENTER", press: "short" } },
  { key: { key: "DEL", press: "short" } },
]);
assert.equal(released({ code: "KeyA" }).prevented, false); // no key code was sent: there is nothing to let go
ws.sent.length = 0;
const left = [
  typed({ key: "v", code: "KeyV", ctrlKey: true }), // a shortcut
  typed({ key: "c", code: "KeyC", metaKey: true }),
  typed({ key: "x", code: "KeyX", altKey: true }),
  typed({ key: "a", code: "KeyA", isComposing: true }), // the IME's
  typed({ key: "Process", code: "KeyA", isComposing: true }),
  typed({ key: "Dead", code: "Quote" }), // not one character
  typed({ key: "Shift", code: "ShiftLeft" }),
  typed({ key: "ArrowLeft", code: "ArrowLeft" }),
  typed({ key: "F5", code: "F5" }),
  typed({ key: "Tab", code: "Tab" }), // the HMI does not declare TAB or ESCAPE: the browser keeps them (focus moves on)
  typed({ key: "Escape", code: "Escape" }),
  typed({ key: "Enter", code: "Enter", ctrlKey: true }),
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

// a change of rotation or of the frame rate is not a new decoder; the canvas follows the shown size
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800,"fps":60}');
assert.equal(decoders[0].configs.length, 2);
assert.equal(canvas.width, 480);
assert.equal(canvas.height, 800);
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800,"fps":30,"rotation":90}');
assert.equal(canvas.width, 800);
assert.equal(decoders[0].configs.length, 2);

// the lag: pictures that arrive later and later are reported (once a second at most), and again when it is gone
ws.sent.length = 0;
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
ws.hangUp(1011, "the video stream broke");
assert.match(elements.status.textContent, /The stream failed: the video stream broke\. Reconnecting in 1 s/);
assert.equal(elements.reconnect.hidden, false);
await sleep(700);
assert.equal(sockets.length, 2);
ws = sockets[1];
ws.open();
ws.receive('{"codec":"avc1.42C01F","width":480,"height":800}');
assert.equal(canvas.width, 480);
await sleep(20);
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

#: A mouse and keyboard HMI (buttons, a wheel, every key code, text): what each event sends, and what a plan that
#: changes leaves behind.
PAGE_MOUSE = r"""
let ws = await openPage();
assert.equal(elements.note.hidden, true);
assert.equal(elements.hint.hidden, false);
assert.equal(elements.controls.hidden, false); // it takes text: there is a box for it
assert.equal(elements["text-form"].hidden, false);
assert.deepEqual(elements.keys.children, []); // and no hard keys
assert.equal(elements.dials.hidden, true); // and no dials
assert.deepEqual(elements.dials.children, []);
assert.equal(canvas.style.cursor, "default");
assert.equal(canvas.listeners.wheel.length, 1);
assert.equal(canvas.listeners.wheel[0].options.passive, false); // it may stop the page from scrolling
const mouse = (type, x, y, buttons, extra = {}) => ({
  pointer: { type, kind: "mouse", x, y, id: 0, buttons, w: 240, h: 400, ...extra },
});

// a click is a down with the primary button and an up; a right click has the secondary one
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointerup", pointer({ clientX: 60, clientY: 100, buttons: 0 }));
canvas.fire("pointerdown", pointer({ button: 2, buttons: 2, clientX: 30, clientY: 50 }));
canvas.fire("pointerup", pointer({ button: 2, buttons: 0, clientX: 30, clientY: 50 }));
assert.deepEqual(ws.sent, [
  mouse("down", 120, 200, 1), mouse("up", 120, 200, 0), mouse("down", 60, 100, 2), mouse("up", 60, 100, 0),
]);
assert.equal(canvas.captured, 1);

// buttons that change while one is down are moves with the buttons as they are after
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 61, clientY: 100 }));
await sleep(30);
canvas.fire("pointermove", pointer({ buttons: 1, clientX: 62, clientY: 100 }));
await sleep(30);
canvas.fire("pointerup", pointer({ clientX: 62, clientY: 100, buttons: 0 }));
assert.deepEqual(ws.sent, [
  mouse("down", 120, 200, 1), mouse("move", 122, 200, 3), mouse("move", 124, 200, 1), mouse("up", 124, 200, 0),
]);

// the first button let go while another is held: the contact stays, with the buttons as they are after; the last one up
// ends it
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 61, clientY: 100 }));
await sleep(30);
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 62, clientY: 100 }));
await sleep(30);
canvas.fire("pointerup", pointer({ button: 2, buttons: 0, clientX: 62, clientY: 100 }));
assert.deepEqual(ws.sent, [
  mouse("down", 120, 200, 1), mouse("move", 122, 200, 3), mouse("move", 124, 200, 2), mouse("up", 124, 200, 0),
]);

// hovering: one move a frame, with no button; none while a button is down; and a hover that was waiting is never sent
// after a press
ws.sent.length = 0;
for (let i = 0; i < 5; i++) canvas.fire("pointermove", pointer({ buttons: 0, clientX: 10 + i, clientY: 20 }));
await sleep(30);
assert.deepEqual(ws.sent, [mouse("move", 28, 40, 0)]); // the newest of the five
ws.sent.length = 0;
canvas.fire("pointermove", pointer({ buttons: 0, clientX: 10, clientY: 20 }));
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
await sleep(30); // the frame in which the hover would have been sent: it would have lifted the mouse
canvas.fire("pointerup", pointer({ clientX: 60, clientY: 100, buttons: 0 }));
assert.deepEqual(ws.sent, [mouse("down", 120, 200, 1), mouse("up", 120, 200, 0)]);
// a touch on a mouse HMI is a mouse, and a finger does not hover
ws.sent.length = 0;
canvas.fire("pointermove", pointer({ pointerType: "touch", pointerId: 5, buttons: 0, clientX: 10, clientY: 20 }));
canvas.fire("pointerdown", pointer({ pointerType: "touch", pointerId: 5, clientX: 60, clientY: 100 }));
canvas.fire("pointerup", pointer({ pointerType: "touch", pointerId: 5, clientX: 60, clientY: 100, buttons: 0 }));
await sleep(30);
assert.deepEqual(ws.sent, [mouse("down", 120, 200, 1), mouse("up", 120, 200, 0)]);

// the wheel: pixels with the sign of the browser's deltas (lines and pages turned into pixels), and the page does not
// scroll
ws.sent.length = 0;
const wheel = (init) =>
  canvas.fire("wheel", { clientX: 60, clientY: 100, deltaX: 0, deltaY: 0, deltaMode: 0, ...init });
assert.equal(wheel({ deltaY: 120 }).prevented, true);
wheel({ deltaX: -5, deltaY: -30 });
wheel({ deltaY: 3, deltaMode: 1 });
wheel({ deltaY: 1, deltaMode: 2 });
wheel({ deltaX: 0, deltaY: 0 }); // nothing to say
const wheelLine = (dx, dy) => ({
  pointer: { type: "wheel", kind: "mouse", x: 120, y: 200, id: 0, dx, dy, w: 240, h: 400 },
});
assert.deepEqual(ws.sent, [wheelLine(0, 120), wheelLine(-5, -30), wheelLine(0, 3 * 40), wheelLine(0, 400)]);

// the keyboard: a key is its code, never text; a repeat is dropped; a key that went down comes up
ws.sent.length = 0;
assert.equal(typed({ key: "a", code: "KeyA" }).prevented, true);
assert.equal(typed({ key: "a", code: "KeyA", repeat: true }).prevented, true);
assert.equal(released({ code: "KeyA" }).prevented, true);
assert.equal(released({ code: "KeyA" }).prevented, false); // already up
assert.equal(typed({ key: "Enter", code: "Enter" }).prevented, true);
released({ code: "Enter" });
assert.deepEqual(ws.sent, [
  { keyboard: { code: "KeyA", type: "down" } },
  { keyboard: { code: "KeyA", type: "up" } },
  { keyboard: { code: "Enter", type: "down" } },
  { keyboard: { code: "Enter", type: "up" } },
]);
// a shortcut is the browser's: Control goes down, the key with it does not, and Control comes up
ws.sent.length = 0;
typed({ key: "Control", code: "ControlLeft", ctrlKey: true });
assert.equal(typed({ key: "r", code: "KeyR", ctrlKey: true }).prevented, false);
released({ code: "KeyR", ctrlKey: true });
released({ code: "ControlLeft" });
assert.deepEqual(ws.sent, [
  { keyboard: { code: "ControlLeft", type: "down" } },
  { keyboard: { code: "ControlLeft", type: "up" } },
]);
// Shift held, then the canvas loses focus: it is let go
ws.sent.length = 0;
typed({ key: "Shift", code: "ShiftLeft", shiftKey: true });
typed({ key: "A", code: "KeyA", shiftKey: true });
canvas.fire("blur");
assert.deepEqual(ws.sent, [
  { keyboard: { code: "ShiftLeft", type: "down" } },
  { keyboard: { code: "KeyA", type: "down" } },
  { keyboard: { code: "ShiftLeft", type: "up" } },
  { keyboard: { code: "KeyA", type: "up" } },
]);
assert.equal(released({ code: "ShiftLeft" }).prevented, false);
// and when the window loses focus, or the page is hidden or left; a pointer that is down is cancelled
const hide = () => {
  document.hidden = true;
  documentListeners.fire("visibilitychange");
};
for (const leave of [() => window.fire("blur"), hide, () => window.fire("pagehide")]) {
  ws.sent.length = 0;
  document.hidden = false;
  typed({ key: "Shift", code: "ShiftRight", shiftKey: true });
  canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
  leave();
  assert.deepEqual(ws.sent, [
    { keyboard: { code: "ShiftRight", type: "down" } },
    mouse("down", 120, 200, 1),
    mouse("cancel", 120, 200, 0),
    { keyboard: { code: "ShiftRight", type: "up" } },
  ]);
}
document.hidden = false;
// text is for the box and the paste; a key the HMI takes by code is not text
ws.sent.length = 0;
elements["text-input"].value = "hello";
elements["text-form"].fire("submit");
document.activeElement = canvas;
documentListeners.fire("paste", { clipboardData: { getData: () => "pasted" } });
assert.deepEqual(ws.sent, [{ text: "hello" }, { text: "pasted" }]);

// the HMI is a different one when the page reconnects (what it declares changed): what was offered goes, and what is
// held is not sent
typed({ key: "Shift", code: "ShiftLeft", shiftKey: true });
info = { input: { pointer: { kinds: [] }, keyboard: { codes: [], text: false }, keys: [], hard_keys: [] } };
ws.hangUp(1006, "");
await sleep(700);
ws = sockets[1];
ws.open();
ws.receive(LINE);
await sleep(20);
assert.deepEqual(ws.sent, []);
assert.equal(canvas.listenerCount(), 0);
assert.equal(window.listenerCount(), 0);
assert.equal(documentListeners.listenerCount(), 0);
assert.equal(elements["text-form"].listenerCount(), 0);
assert.equal(elements["text-form"].hidden, true);
assert.equal(elements.hint.hidden, true);
assert.match(elements.note.textContent, /View only/);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: A mouse that has the primary button only (no buttons, no wheel): the other buttons are not the HMI's, and the contact
#: follows the primary one however the browser orders them.
PAGE_MOUSE_PRIMARY = r"""
const ws = await openPage();
assert.equal(canvas.listeners.wheel, undefined);
const mouse = (type, x, y, buttons) => ({ pointer: { type, kind: "mouse", x, y, id: 0, buttons, w: 240, h: 400 } });

// a button that is not declared does not stop the hover: the pointer moves with no button
ws.sent.length = 0;
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 10, clientY: 20 }));
await sleep(30);
assert.deepEqual(ws.sent, [mouse("move", 20, 40, 0)]);

// right down (a pointerdown), left down (a move), left up while right is held (a move), left down again, right up (a
// move) and left up: the contact is the left button's alone
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ button: 2, buttons: 2, clientX: 30, clientY: 40 }));
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 30, clientY: 40 }));
canvas.fire("pointermove", pointer({ buttons: 2, clientX: 32, clientY: 40 }));
canvas.fire("pointermove", pointer({ buttons: 3, clientX: 34, clientY: 40 }));
canvas.fire("pointermove", pointer({ buttons: 1, clientX: 36, clientY: 40 }));
await sleep(30);
canvas.fire("pointerup", pointer({ buttons: 0, clientX: 38, clientY: 40 }));
assert.deepEqual(ws.sent, [
  mouse("down", 60, 80, 1), mouse("up", 64, 80, 0), mouse("down", 68, 80, 1), mouse("move", 72, 80, 1),
  mouse("up", 76, 80, 0),
]);
assert.equal(canvas.captured, 1);

// a button of the browser's that is not the HMI's, pressed and released, is nothing
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ button: 2, buttons: 2, clientX: 30, clientY: 40 }));
canvas.fire("pointerup", pointer({ button: 2, buttons: 0, clientX: 30, clientY: 40 }));
await sleep(30);
assert.deepEqual(ws.sent, []);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: An HMI that takes nothing: the picture, a note, and no input handling at all.
PAGE_VIEW_ONLY = r"""
const ws = await openPage();
assert.match(elements.status.textContent, /Live|Waiting/);
assert.equal(elements.note.hidden, false);
assert.match(elements.note.textContent, /^View only/);
assert.equal(elements.hint.hidden, true);
assert.equal(elements.controls.hidden, true);
assert.equal(elements.dials.hidden, true);
assert.deepEqual(elements.dials.children, []);
assert.equal(elements["text-form"].hidden, true);
assert.equal(canvas.tabIndex, -1);
assert.equal(canvas.style.cursor, "default");
// nothing listens: not the picture, not the window, not the document, not the text box
assert.equal(canvas.listenerCount(), 0);
assert.equal(window.listenerCount(), 0);
assert.equal(documentListeners.listenerCount(), 0);
assert.equal(elements["text-form"].listenerCount(), 0);
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointerup", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("wheel", { clientX: 60, clientY: 100, deltaY: 100 });
typed({ key: "a", code: "KeyA" });
documentListeners.fire("paste", { clipboardData: { getData: () => "pasted" } });
await sleep(30);
assert.deepEqual(ws.sent, []);
// it still shows the picture
ws.receive(media(1_700_000_000_000_000, keyUnit));
await sleep(10);
assert.equal(decoders[0].chunks.length >= 1, true);
assert.equal(canvas.hidden, false);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: A pointer that takes relative motion only: this page sends positions, so it says so and sends no pointer at all; the
#: keyboard the HMI also has still works.
PAGE_RELATIVE_ONLY = r"""
const ws = await openPage();
assert.equal(elements.note.hidden, false);
assert.match(elements.note.textContent, /relative pointer motion only/);
assert.equal(canvas.listeners.pointerdown, undefined);
assert.equal(canvas.listeners.pointermove, undefined);
assert.equal(canvas.listeners.wheel, undefined);
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointermove", pointer({ buttons: 0, clientX: 10, clientY: 20 }));
await sleep(30);
assert.deepEqual(ws.sent, []);
typed({ key: "a", code: "KeyA" });
assert.deepEqual(ws.sent, [{ keyboard: { code: "KeyA", type: "down" } }]);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: Several fingers, and an HMI that takes a list of key codes and text: the contacts the page hands out, and the key
#: that is not on the list.
PAGE_MULTI_TOUCH = r"""
const ws = await openPage();
canvas.rect = { left: 0, top: 0, width: 240, height: 400 };
const finger = (type, id, x, y, buttons = 1) => ({
  pointer: { type, kind: "touch", x, y, id, buttons, w: 240, h: 400 },
});
const touch = (pointerId, x, y, extra = {}) =>
  pointer({ pointerType: "touch", pointerId, isPrimary: pointerId === 10, clientX: x, clientY: y, ...extra });
ws.sent.length = 0;
canvas.fire("pointerdown", touch(10, 10, 10));
canvas.fire("pointerdown", touch(11, 20, 20));
canvas.fire("pointerdown", touch(12, 30, 30));
canvas.fire("pointerdown", touch(13, 40, 40)); // the HMI follows three
assert.deepEqual(ws.sent, [finger("down", 0, 10, 10), finger("down", 1, 20, 20), finger("down", 2, 30, 30)]);
ws.sent.length = 0;
canvas.fire("pointermove", touch(11, 25, 25));
canvas.fire("pointermove", touch(10, 15, 15));
canvas.fire("pointermove", touch(13, 45, 45)); // not down: nothing
await sleep(30);
assert.deepEqual(ws.sent, [finger("move", 1, 25, 25), finger("move", 0, 15, 15)]);
ws.sent.length = 0;
canvas.fire("pointerup", touch(10, 15, 15, { buttons: 0 }));
canvas.fire("pointerdown", touch(14, 50, 50)); // it takes the contact that was let go
assert.deepEqual(ws.sent, [finger("up", 0, 15, 15, 0), finger("down", 0, 50, 50)]);
ws.sent.length = 0;
window.fire("blur"); // all that are down are cancelled
const cancelled = ws.sent.map((m) => [m.pointer.type, m.pointer.id]).sort();
assert.deepEqual(cancelled, [["cancel", 0], ["cancel", 1], ["cancel", 2]]);
// a key that is on the list is its code; one that is not is text; a key that is neither is the browser's
ws.sent.length = 0;
typed({ key: "a", code: "KeyA" });
typed({ key: "b", code: "KeyB" });
assert.equal(typed({ key: "ArrowUp", code: "ArrowUp" }).prevented, false);
assert.deepEqual(ws.sent, [{ keyboard: { code: "KeyA", type: "down" } }, { text: "b" }]);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: An HMI with rotaries: a dial for each, the wheel over it, the two buttons and the arrow keys turning it, and the
#: push button that is a hard key's button; the push keys are not drawn among the hard keys.
PAGE_DIALS = r"""
let ws = await openPage();
assert.equal(elements.dials.hidden, false);
assert.equal(elements.controls.hidden, false);
assert.equal(elements.note.hidden, true); // it is not view only
const dials = elements.dials.children;
assert.deepEqual(dials.map((d) => d.attributes["aria-label"]), ["Volume", "Tune", "Bass"]);
assert.deepEqual(dials.map((d) => d.attributes.role), ["group", "group", "group"]);
// the hard keys: the push keys are with their dial and not drawn again
assert.deepEqual(elements.keys.children.map((b) => b.textContent), ["Home"]);
const part = (dial, name) => dial.children.find((c) => c.className === name);
const [volume, tune, bass] = dials;
for (const dial of dials) {
  const label = dial.attributes["aria-label"];
  assert.deepEqual(dial.children.map((c) => c.className).slice(0, 4), ["knob", "ccw", "cw", "name"]);
  assert.equal(part(dial, "knob").attributes["aria-label"], label);
  assert.equal(part(dial, "knob").attributes.role, "application");
  assert.equal(part(dial, "knob").tabIndex, 0); // it takes the focus
  assert.equal(part(dial, "ccw").attributes["aria-label"], `${label}: counter-clockwise`);
  assert.equal(part(dial, "cw").attributes["aria-label"], `${label}: clockwise`);
  assert.equal(part(dial, "name").textContent, label);
}
assert.equal(part(volume, "push").textContent, "Push");
assert.equal(part(volume, "push").attributes["aria-label"], "Volume: push");
assert.equal(part(tune, "push").attributes["aria-label"], "Tune: push");
assert.equal(part(bass, "push"), undefined); // no push key declared: no push button
assert.equal(volume.children.length, 5);
assert.equal(bass.children.length, 4);
// each dial listens for the wheel (it may stop the page from scrolling) and the keys, and nothing else
assert.deepEqual(Object.keys(volume.listeners).filter((t) => volume.listeners[t].length).sort(), ["keydown", "wheel"]);
assert.equal(volume.listeners.wheel[0].options.passive, false);

const rotary = (id, delta) => ({ rotary: { id, delta } });
const wheel = (dial, init) => dial.fire("wheel", { deltaX: 0, deltaY: 0, deltaMode: 0, ...init });

// the wheel: one detent for each 100 pixels, scrolling up is clockwise, what is less than a detent is kept for the
// next turn, and the page does not scroll
ws.sent.length = 0;
assert.equal(wheel(volume, { deltaY: -100 }).prevented, true);
assert.equal(wheel(volume, { deltaY: -40 }).prevented, true);
assert.equal(wheel(volume, { deltaY: -60 }).prevented, true);
wheel(volume, { deltaY: 30 });
wheel(volume, { deltaY: 20 });
wheel(volume, { deltaY: 60 }); // 110 pixels the other way: one detent, and 10 pixels left
wheel(volume, { deltaY: -90 }); // 80 pixels
wheel(volume, { deltaY: -20 }); // 100
wheel(volume, { deltaX: 400, deltaY: 0 }); // sideways turns nothing, but the page does not scroll either
assert.deepEqual(ws.sent, [rotary("volume", 1), rotary("volume", 1), rotary("volume", -1), rotary("volume", 1)]);
assert.equal(wheel(volume, { deltaX: 400 }).prevented, true);
// lines and pages are turned into pixels (a line is 40, a page the shown picture's height); the dials are kept apart
ws.sent.length = 0;
wheel(tune, { deltaY: -3, deltaMode: 1 }); // 120 pixels: a detent, and 20 left
wheel(tune, { deltaY: -3, deltaMode: 1 }); // 140: another, and 40 left
wheel(tune, { deltaY: 1, deltaMode: 2 }); // a page (the picture is 400 high) back: 360 pixels, three detents
wheel(bass, { deltaY: -50 }); // not one yet
assert.deepEqual(ws.sent, [rotary("tune", 1), rotary("tune", 1), rotary("tune", -3)]);
// a turn of more than the exporter takes in one line is several
ws.sent.length = 0;
wheel(bass, { deltaY: -25000 }); // 250 detents (and the 50 pixels from before are in them)
assert.deepEqual(ws.sent, [rotary("bass", 100), rotary("bass", 100), rotary("bass", 50)]);

// the two buttons: a detent for a click, by pointer or by keyboard
ws.sent.length = 0;
part(volume, "cw").fire("click", { detail: 1 });
part(volume, "ccw").fire("click", { detail: 1 });
part(volume, "ccw").fire("click", { detail: 0 });
part(bass, "cw").fire("click", { detail: 1 });
assert.deepEqual(ws.sent, [rotary("volume", 1), rotary("volume", -1), rotary("volume", -1), rotary("bass", 1)]);

// the arrow keys, when the dial has focus: right and up clockwise, left and down not; a held key repeats; the page
// does not scroll
ws.sent.length = 0;
const arrow = (dial, key, init = {}) =>
  dial.fire("keydown", { key, code: key, ctrlKey: false, metaKey: false, altKey: false, ...init });
assert.equal(arrow(volume, "ArrowRight").prevented, true);
assert.equal(arrow(volume, "ArrowUp").prevented, true);
assert.equal(arrow(volume, "ArrowLeft").prevented, true);
assert.equal(arrow(volume, "ArrowDown").prevented, true);
assert.equal(arrow(tune, "ArrowRight", { repeat: true }).prevented, true);
assert.deepEqual(ws.sent, [
  rotary("volume", 1), rotary("volume", 1), rotary("volume", -1), rotary("volume", -1), rotary("tune", 1),
]);
ws.sent.length = 0;
assert.equal(arrow(volume, "ArrowRight", { ctrlKey: true }).prevented, false); // a shortcut is the browser's
assert.equal(arrow(volume, "ArrowLeft", { altKey: true }).prevented, false);
assert.equal(arrow(volume, "KeyA").prevented, false);
assert.equal(arrow(volume, "Enter").prevented, false);
assert.deepEqual(ws.sent, []);
// the notch follows the turns: 15 degrees a detent
assert.equal(part(bass, "knob").style.transform, "rotate(165deg)"); // 251 detents of 15 degrees, turned round

// the push button is a hard key's button: a click is a short press, held it is a long one where the key takes it
ws.sent.length = 0;
const push = part(volume, "push");
push.fire("pointerdown", pointer({}));
push.fire("pointerup", pointer({}));
push.fire("click", { detail: 1 }); // the click that follows a pointer: nothing more
push.fire("pointerdown", pointer({}));
await sleep(700);
push.fire("pointerup", pointer({}));
push.fire("click", { detail: 0 }); // from the keyboard
assert.deepEqual(ws.sent, [
  { key: { key: "VOLUME_PUSH", press: "short" } },
  { key: { key: "VOLUME_PUSH", press: "long" } },
  { key: { key: "VOLUME_PUSH", press: "short" } },
]);
// a push key that takes a short press only sends that, held or not
ws.sent.length = 0;
const tunePush = part(tune, "push");
tunePush.fire("pointerdown", pointer({}));
await sleep(700);
tunePush.fire("pointerup", pointer({}));
assert.deepEqual(ws.sent, [{ key: { key: "TUNE_PUSH", press: "short" } }]);
// the other hard key buttons are as before
ws.sent.length = 0;
elements.keys.children[0].fire("click", { detail: 0 });
assert.deepEqual(ws.sent, [{ key: { key: "HOME", press: "short" } }]);

// the picture still takes the pointer
ws.sent.length = 0;
canvas.fire("pointerdown", pointer({ clientX: 60, clientY: 100 }));
canvas.fire("pointerup", pointer({ clientX: 60, clientY: 100, buttons: 0 }));
assert.equal(ws.sent.length, 2);

// the HMI is a different one when the page reconnects (a rotary is gone): its dials go, and nothing listens for them
info = {
  ...info,
  input: { ...info.input, rotaries: [{ id: "tune", label: "Tune", push: "TUNE_PUSH" }] },
};
ws.hangUp(1006, "");
await sleep(700);
ws = sockets[1];
ws.open();
ws.receive(LINE);
await sleep(20);
assert.deepEqual(elements.dials.children.map((d) => d.attributes["aria-label"]), ["Tune"]);
assert.equal(volume.listenerCount(), 0);
assert.equal(tune.listenerCount(), 0);
assert.equal(part(volume, "cw").listenerCount(), 0);
const [tuneAgain] = elements.dials.children;
ws.sent.length = 0;
wheel(tuneAgain, { deltaY: -100 });
assert.deepEqual(ws.sent, [rotary("tune", 1)]);
// and an HMI that declares none has no dial at all
info = { ...info, input: { ...info.input, rotaries: [] } };
ws.hangUp(1006, "");
await sleep(700);
ws = sockets[2];
ws.open();
ws.receive(LINE);
await sleep(20);
assert.equal(elements.dials.hidden, true);
assert.deepEqual(elements.dials.children, []);
assert.equal(tuneAgain.listenerCount(), 0);

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""

#: The codecs: what a codec line starts, what the browser cannot decode, and a change of codec or family in mid-stream.
PAGE_CODECS = r"""
const ws = await openPage();
assert.equal(decoders.length, 1);
const failures = () => elements.errors.children.map((e) => e.textContent);

// HEVC in a browser that cannot decode it: a clear message that names the codec and says what to ask for, and no
// fallback
VideoDecoder.supports = (config) => !config.codec.startsWith("hev");
ws.receive(HEVC_LINE);
await sleep(20);
assert.deepEqual(checked.at(-1), {
  codec: "hev1.1.6.L93.B0", codedWidth: 240, codedHeight: 400, optimizeForLatency: true, hevc: { format: "annexb" },
});
assert.match(elements.status.textContent, /cannot decode HEVC \(hev1\.1\.6\.L93\.B0\).*--codec h264/);
assert.equal(canvas.hidden, true);
assert.equal(decoders.length, 1);
assert.equal(decoders[0].wasClosed, true); // the H.264 decoder is not left running behind it
ws.receive(media(1_700_000_000_000_000, hevcKeyUnit));
await sleep(10);
assert.equal(decoders[0].chunks.length, 0);
assert.equal(bitmaps.length, 0);
assert.deepEqual(failures(), []); // it is the status line's to say, not an error that fades
// the same line again (a rotation, say) does not clear it
ws.receive('{"codec":"hev1.1.6.L93.B0","width":240,"height":400,"rotation":90}');
assert.match(elements.status.textContent, /cannot decode HEVC/);
assert.equal(canvas.hidden, true);

// then H.264: the message goes, the picture comes
ws.receive('{"codec":"avc1.42C01F","width":240,"height":400,"fps":15}');
assert.doesNotMatch(elements.status.textContent, /cannot decode/);
ws.receive(media(1_700_000_000_100_000, keyUnit));
await sleep(20);
assert.equal(canvas.hidden, false);
assert.equal(decoders.length, 2);
assert.deepEqual(decoders[1].chunks.map((c) => c.type), ["key"]);

// HEVC in a browser that can: Annex-B, a key picture is an IRAP picture, and the one decoder is reconfigured
VideoDecoder.supports = () => true;
ws.receive(HEVC_LINE);
ws.receive(media(1_700_000_000_200_000, hevcDeltaUnit)); // a delta picture before the key one
ws.receive(media(1_700_000_000_233_000, hevcKeyUnit));
ws.receive(media(1_700_000_000_266_000, hevcDeltaUnit));
await sleep(20);
assert.equal(decoders.length, 2);
assert.equal(decoders[1].configs.at(-1).codec, "hev1.1.6.L93.B0");
assert.deepEqual(decoders[1].configs.at(-1).hevc, { format: "annexb" });
assert.deepEqual(decoders[1].chunks.slice(1).map((c) => c.type), ["key", "delta"]);
assert.match(elements.status.textContent, /hev1\.1\.6\.L93\.B0/);

// MJPEG: no decoder; every unit is a JPEG picture, handed to createImageBitmap, and drawn in order
const drawnSizes = () => canvas.calls.filter(([name]) => name === "drawImage").map((call) => call[1].size);
canvas.calls.length = 0;
ws.receive(MJPEG_LINE);
assert.equal(decoders[1].wasClosed, true);
ws.receive(media(1_700_000_000_300_000, jpegUnit(1)));
await sleep(10);
ws.receive(media(1_700_000_000_333_000, jpegUnit(2)));
await sleep(10);
assert.deepEqual(bitmaps.map((b) => [b.size, b.type]), [[5, "image/jpeg"], [6, "image/jpeg"]]);
assert.deepEqual(drawnSizes(), [5, 6]);
assert.equal(bitmaps.every((b) => b.bitmap.closed === true), true); // each is closed once it is drawn
assert.match(elements.status.textContent, /Live: 240×400.*mjpeg/);

// a browser that is behind drops the stale pictures: only the newest of those that came meanwhile is decoded
bitmaps.length = 0;
canvas.calls.length = 0;
holdBitmaps = true;
for (const n of [11, 12, 13, 14, 15]) ws.receive(media(1_700_000_000_400_000 + n, jpegUnit(n)));
await sleep(10);
assert.deepEqual(bitmaps.map((b) => b.size), [15]);
bitmaps[0].resolve();
await sleep(10);
assert.deepEqual(bitmaps.map((b) => b.size), [15, 19]);
bitmaps[1].resolve();
await sleep(10);
assert.deepEqual(drawnSizes(), [15, 19]); // in order, and without the three in between
// a picture that is decoded after the stream changed under it is not drawn
bitmaps.length = 0;
canvas.calls.length = 0;
ws.receive(media(1_700_000_000_500_000, jpegUnit(21)));
await sleep(10);
ws.receive('{"codec":"avc1.42C01F","width":240,"height":400,"fps":15}');
bitmaps[0].resolve();
await sleep(10);
assert.deepEqual(drawnSizes(), []);
assert.equal(bitmaps[0].bitmap.closed, true);
holdBitmaps = false;

// a picture that cannot be decoded is said once, and the next one is shown
ws.receive(MJPEG_LINE);
bitmaps.length = 0;
canvas.calls.length = 0;
holdBitmaps = true;
ws.receive(media(1_700_000_000_600_000, jpegUnit(31)));
await sleep(10);
bitmaps[0].reject(new Error("the picture could not be decoded"));
await sleep(10);
holdBitmaps = false;
ws.receive(media(1_700_000_000_633_000, jpegUnit(32)));
await sleep(10);
assert.deepEqual(failures(), ["A picture could not be decoded: the picture could not be decoded"]);
assert.deepEqual(drawnSizes(), [36]);

// a rotation is not a new start: the pictures go on, turned
ws.receive('{"codec":"mjpeg","width":240,"height":400,"rotation":90}');
assert.equal(canvas.width, 400);
canvas.calls.length = 0;
ws.receive(media(1_700_000_000_700_000, jpegUnit(41)));
await sleep(10);
assert.deepEqual(drawnSizes(), [45]);
assert.ok(Math.abs(canvas.calls.find(([name]) => name === "rotate")[1] - Math.PI / 2) < 1e-12);

// a codec the page does not know
ws.receive('{"codec":"vp09.00.10.08","width":240,"height":400}');
assert.match(elements.status.textContent, /does not know the codec vp09\.00\.10\.08/);
assert.equal(canvas.hidden, true);

// no WebCodecs at all: H.264 cannot be shown and the message says which browsers can, but MJPEG still is
const real = globalThis.VideoDecoder;
delete globalThis.VideoDecoder;
ws.receive(LINE);
await sleep(10);
assert.match(elements.status.textContent, /cannot decode H\.264.*Chrome/);
ws.receive(MJPEG_LINE);
canvas.calls.length = 0;
ws.receive(media(1_700_000_000_800_000, jpegUnit(51)));
await sleep(10);
assert.deepEqual(drawnSizes(), [55]);
assert.match(elements.status.textContent, /Live|Waiting/);
globalThis.VideoDecoder = real;

console.log(JSON.stringify({ ok: true }));
process.exit(0);
"""


def _info(**capabilities: Any) -> dict:
    """What ``/info`` answers for an HMI of these capabilities: the interface's own ``hmi_info()``."""
    return Capabilities(**capabilities).info(width=240, height=400)


#: What each page scenario's HMI declares.
PAGE_INFOS = {
    "touch": _info(
        pointer=PointerCaps.touch(),
        keyboard=KeyboardCaps(text=True),
        keys=("HOME", "POWER", "ENTER", "DEL"),
        hard_keys=(HardKey("HOME", "Home"), HardKey("POWER", "Power", ("long",))),
    ),
    "mouse": _info(pointer=PointerCaps.mouse(), keyboard=KeyboardCaps(codes="all", text=True)),
    "view only": _info(),
    "relative only": _info(
        pointer=PointerCaps(kinds=("mouse",), absolute=False, relative=True, buttons=True, max_contacts=1),
        keyboard=KeyboardCaps(codes=("KeyA",)),
    ),
    "multi touch": _info(pointer=PointerCaps.touch(max_contacts=3), keyboard=KeyboardCaps(codes=("KeyA",), text=True)),
    "mouse primary": _info(pointer=PointerCaps.mouse(buttons=False, wheel=False)),
    "dials": _info(
        pointer=PointerCaps.touch(),
        keys=("HOME", "VOLUME_PUSH", "TUNE_PUSH"),
        hard_keys=(
            HardKey("HOME", "Home"),
            HardKey("VOLUME_PUSH", "Volume Push"),
            HardKey("TUNE_PUSH", "Tune Push", ("short",)),
        ),
        rotaries=(
            RotaryCaps("volume", "Volume", "VOLUME_PUSH"),
            RotaryCaps("tune", "Tune", "TUNE_PUSH"),
            RotaryCaps("bass"),
        ),
    ),
    "codecs": _info(codecs=("h264", "hevc", "mjpeg"), pointer=PointerCaps.touch()),
}
PAGE_BODIES = {
    "touch": PAGE_TOUCH,
    "mouse": PAGE_MOUSE,
    "view only": PAGE_VIEW_ONLY,
    "relative only": PAGE_RELATIVE_ONLY,
    "multi touch": PAGE_MULTI_TOUCH,
    "mouse primary": PAGE_MOUSE_PRIMARY,
    "dials": PAGE_DIALS,
    "codecs": PAGE_CODECS,
}


def run_page(module: Path, name: str, search: str = "?token=abc-DEF_12") -> None:
    """Run a page scenario under node: the prelude, then the scenario, against the page's script."""
    assert "/tmp" not in PAGE_PRELUDE
    script = PAGE_PRELUDE.replace("__INFO__", json.dumps(PAGE_INFOS[name])) + PAGE_BODIES[name]
    script = script.replace("__MODULE__", module.as_uri()).replace("__SEARCH__", search)
    result = subprocess.run(
        [node(), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"ok": True}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("name", list(PAGE_BODIES))
def test_the_page_decodes_draws_and_sends_what_the_hmi_declares(module: Path, name: str) -> None:
    run_page(module, name)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_page_opened_without_the_token_connects_to_nothing(module: Path) -> None:
    run_page(module, "touch", search="")


def test_the_page_finds_its_elements_and_none_of_the_old_names_are_left() -> None:
    page = (STATIC / "index.html").read_text()
    script = (STATIC / "hmi.js").read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    assert set(re.findall(r'\$\("([^"]+)"\)', script)) <= ids  # every element the script looks for is there
    for selector in re.findall(r"^\s*#([\w-]+)[^{]*\{", page, re.MULTILINE):  # and every CSS rule is for one that is
        assert selector in ids, selector
    for text in (page, script, (Path(__file__).parent / "server.py").read_text()):
        assert not re.search(r"\bscreen\b", text, re.IGNORECASE)  # it is an HMI, and its picture is not "the screen"


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
            assert "Stopping the HMI view" in rest
            assert process.wait(timeout=30) == 0
            with pytest.raises(urllib.error.URLError):
                urllib.request.urlopen(url.group(), timeout=5)
        finally:
            process.kill()

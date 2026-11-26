"""Tests for the driver context, stream and property keywords."""

from __future__ import annotations

import socket
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from dataclasses import dataclass, field

import anyio
import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import EchoNetwork
from jumpstarter_driver_power.driver import MockPower
from robot.api import Failure
from robot.utils import DotDict

from .. import context
from ..conftest import write
from ..testing import use_client
from .contexts import ContextKeywords, open_contexts
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Recorder(Driver):
    """Records what clients acquire and release."""

    events: list[str] = field(default_factory=list)

    @classmethod
    def client(cls) -> str:
        return f"{__name__}.RecorderClient"

    @export
    def acquire(self, name: str) -> str:
        self.events.append(f"acquire:{name}")
        return f"token-{name}"

    @export
    def release(self, token: str) -> None:
        self.events.append(f"release:{token}")

    @export
    def get_events(self) -> list[str]:
        return list(self.events)

    @export
    async def ticks(self, start: int = 0) -> AsyncGenerator[int, None]:
        n = start
        while True:
            yield n
            n += 1
            await anyio.sleep(0.01)


class RecorderClient(DriverClient):
    @contextmanager
    def hold(self, name: str = "x"):
        token = self.call("acquire", name)
        try:
            yield token
        finally:
            self.call("release", token)

    @contextmanager
    def forward(self, port: int = 5037):
        yield ("127.0.0.1", port)

    def get_events(self) -> list[str]:
        return self.call("get_events")

    @property
    def label(self) -> str:
        return "recorder"


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def client(recorder):
    children = {"rec": recorder, "power": MockPower(), "echo": EchoNetwork()}
    with serve(Composite(children=children)) as root, use_client(root):
        try:
            yield root
        finally:
            context.end_run()


@pytest.fixture
def keywords():
    return ContextKeywords()


def test_open_returns_yielded_value_and_close_exits(client, keywords, recorder):
    assert keywords.open_driver_context("rec", "hold", "a") == "token-a"
    assert [c.alias for c in open_contexts()] == ["hold"]
    assert keywords.get_driver_context_value("hold") == "token-a"
    keywords.close_driver_context("hold")
    assert recorder.events == ["acquire:a", "release:token-a"]
    assert open_contexts() == []


def test_tuple_value_becomes_a_list(client, keywords):
    assert keywords.open_driver_context("rec", "forward", port=1234) == ["127.0.0.1", 1234]


def test_alias_in_use_fails(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "first", alias="mine")
    with pytest.raises(Failure, match="'mine' is already open"):
        keywords.open_driver_context("rec", "hold", "second", alias="mine")
    assert recorder.events == ["acquire:first"]


def test_close_all_closes_most_recent_first(client, keywords, recorder):
    for name in ("a", "b"):
        keywords.open_driver_context("rec", "hold", name, alias=name)
    keywords.close_all_driver_contexts()
    assert recorder.events[2:] == ["release:token-b", "release:token-a"]
    with pytest.raises(Failure, match="no driver context is open"):
        keywords.close_driver_context()


def test_open_errors(client, keywords):
    with pytest.raises(Failure, match="did not return a context manager"):
        keywords.open_driver_context("rec", "get_events")
    with pytest.raises(Failure, match="has no method 'missing'"):
        keywords.open_driver_context("rec", "missing")
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.open_driver_context("rec", "hold", scope="forever")
    assert open_contexts() == []


def test_global_contexts_close_at_the_end_of_the_run(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "g", scope="global")
    context.end_test()
    context.end_suite("s1")
    assert recorder.events == ["acquire:g"]
    context.end_run()
    assert recorder.events == ["acquire:g", "release:token-g"]


def test_collect_stream_by_count_and_timeout(client, keywords):
    assert keywords.collect_driver_stream("rec", "ticks", 5, count=3, timeout=None) == [5, 6, 7]
    assert keywords.collect_driver_stream("rec", "ticks", timeout="0.2s")[:2] == [0, 1]
    readings = keywords.collect_driver_stream("power", "read", timeout="5s")  # a stream that ends
    assert [r.voltage for r in readings] == [0.0, 5.0]
    assert isinstance(readings[0], DotDict)


def test_collect_needs_a_limit(client, keywords):
    with pytest.raises(Failure, match="needs count, timeout or both"):
        keywords.collect_driver_stream("rec", "ticks", count=None, timeout=None)


def test_get_property(client, keywords):
    assert keywords.get_driver_property("rec", "label") == "recorder"
    with pytest.raises(Failure, match="has no property 'nope'"):
        keywords.get_driver_property("rec", "nope")


def test_port_forward_reaches_the_driver_stream(client, keywords):
    address = keywords.open_port_forward("echo", alias="echo")
    host, port = address.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(b"ping")
        assert sock.recv(4) == b"ping"
    keywords.close_driver_context("echo")
    with pytest.raises(OSError):
        socket.create_connection((host, int(port)), timeout=1).close()


SUITE = """
    *** Settings ***
    Library    jumpstarter_robot.Jumpstarter

    *** Test Cases ***
    Test Scope Context
        ${token}=    Open Driver Context    rec    hold    test    scope=test
        Should Be Equal    ${token}    token-test

    Test Scope Was Closed At End Of Previous Test
        ${events}=    Call Driver    rec    get_events
        Should Contain    ${events}    release:token-test

    Stream And Property
        ${ticks}=    Collect Driver Stream    rec    ticks    ${10}    count=2    timeout=None
        Should Be Equal As Integers    ${ticks}[1]    11
        ${label}=    Get Driver Property    rec    label
        Should Be Equal    ${label}    recorder
"""


def test_robot_suite(tmp_path, run_robot, client):
    run_robot(write(tmp_path, "contexts.robot", SUITE)).assert_passed()
    assert open_contexts() == []

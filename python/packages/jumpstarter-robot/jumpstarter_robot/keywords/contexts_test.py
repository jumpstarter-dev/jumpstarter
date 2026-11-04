"""Tests for the driver context, stream and property keywords."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.lowlevel
import pytest
import robot
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from pydantic import BaseModel
from robot.api import ExecutionResult, Failure
from robot.utils import DotDict

from .. import context, sessions
from ..testing import use_client
from .contexts import ContextKeywords, open_contexts
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


class Info(BaseModel):
    serial: str
    ports: list[int]


@dataclass(kw_only=True)
class Recorder(Driver):
    """Records what clients acquire and release, and how its streams end."""

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
        self.events.append("ticks:start")
        try:
            n = start
            while True:
                yield n
                n += 1
                await anyio.sleep(0.01)
        finally:
            self.events.append("ticks:stop")

    @export
    async def burst(self) -> AsyncGenerator[int, None]:
        """Endless and as fast as possible, so items are buffered when the reader stops."""
        self.events.append("burst:start")
        try:
            n = 0
            while True:
                yield n
                n += 1
                await anyio.lowlevel.checkpoint()
        finally:
            self.events.append("burst:stop")

    @export
    async def slow(self) -> AsyncGenerator[int, None]:
        self.events.append("slow:start")
        try:
            yield 1
            await anyio.sleep_forever()
        finally:
            self.events.append("slow:stop")

    @export
    async def failing(self) -> AsyncGenerator[int, None]:
        yield 1
        raise ValueError("stream broke")

    @export
    async def models(self) -> AsyncGenerator[Info, None]:
        yield Info(serial="abc", ports=[1, 2])


class RecorderClient(DriverClient):
    info_calls = 0
    enters: ClassVar[list[str]] = []

    @asynccontextmanager
    async def hold_forever(self):
        """An enter that never finishes, like a console waiting for a prompt that never comes."""
        self.enters.append("entering")
        try:
            await anyio.sleep_forever()
        finally:
            self.enters.append("cancelled")
        yield  # pragma: no cover

    @contextmanager
    def hold(self, name: str = "x"):
        token = self.call("acquire", name)
        try:
            yield token
        finally:
            self.call("release", token)

    @asynccontextmanager
    async def hold_async(self, name: str = "async"):
        token = await self.call_async("acquire", name)
        try:
            yield token
        finally:
            await self.call_async("release", token)

    @contextmanager
    def forward(self, port: int = 5037):
        yield ("127.0.0.1", port)

    @contextmanager
    def model(self):
        yield Info(serial="xyz", ports=[7])

    @contextmanager
    def broken_exit(self):
        yield "ok"
        raise RuntimeError("exit failed")

    @contextmanager
    def broken_enter(self):
        raise RuntimeError("enter failed")
        yield  # pragma: no cover

    def get_events(self) -> list[str]:
        return self.call("get_events")

    def not_a_context(self) -> int:
        return 5

    @property
    def label(self) -> str:
        return "recorder"

    @property
    def pair(self) -> tuple[str, int]:
        return ("host", 22)

    @cached_property
    def info(self) -> Info:
        type(self).info_calls += 1
        return Info(serial="cached", ports=[3])


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def client(recorder):
    with serve(Composite(children={"rec": recorder, "power": MockPower()})) as root, use_client(root):
        try:
            yield root
        finally:
            context.end_run()


@pytest.fixture
def keywords():
    return ContextKeywords()


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ---- Open / Close Driver Context ----


def test_open_returns_yielded_value_and_close_exits(client, keywords, recorder):
    assert keywords.open_driver_context("rec", "hold", "a") == "token-a"
    assert recorder.events == ["acquire:a"]
    assert [c.alias for c in open_contexts()] == ["hold"]
    assert keywords.get_driver_context_value("hold") == "token-a"
    keywords.close_driver_context("hold")
    assert recorder.events == ["acquire:a", "release:token-a"]
    assert open_contexts() == []


def test_kwargs_are_passed_to_the_method(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", name="kw")
    assert recorder.events == ["acquire:kw"]


def test_tuple_becomes_list_and_model_becomes_dotdict(client, keywords):
    assert keywords.open_driver_context("rec", "forward", port=1234) == ["127.0.0.1", 1234]
    value = keywords.open_driver_context("rec", "model")
    assert isinstance(value, DotDict)
    assert value.serial == "xyz" and value.ports == [7]


def test_async_context_manager(client, keywords, recorder):
    assert keywords.open_driver_context("rec", "hold_async", alias="a") == "token-async"
    keywords.close_driver_context("a")
    assert recorder.events == ["acquire:async", "release:token-async"]


def test_default_alias_is_uniquified(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "one")
    keywords.open_driver_context("rec", "hold", "two")
    keywords.open_driver_context("rec", "hold", "three")
    assert [c.alias for c in open_contexts()] == ["hold", "hold_2", "hold_3"]
    assert keywords.get_driver_context_value("hold_2") == "token-two"
    keywords.close_driver_context("hold_2")
    assert [c.alias for c in open_contexts()] == ["hold", "hold_3"]
    keywords.open_driver_context("rec", "hold", "four")
    assert [c.alias for c in open_contexts()] == ["hold", "hold_3", "hold_2"]


def test_explicit_alias_in_use_fails_without_entering(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "first", alias="mine")
    with pytest.raises(Failure, match="'mine' is already open"):
        keywords.open_driver_context("rec", "hold", "second", alias="mine")
    assert recorder.events == ["acquire:first"]
    assert keywords.get_driver_context_value("mine") == "token-first"


def test_close_without_alias_closes_most_recent(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "a", alias="a")
    keywords.open_driver_context("rec", "hold", "b", alias="b")
    assert keywords.get_driver_context_value() == "token-b"
    keywords.close_driver_context()
    assert recorder.events[-1] == "release:token-b"
    keywords.close_driver_context()
    assert recorder.events[-1] == "release:token-a"
    with pytest.raises(Failure, match="no driver context is open"):
        keywords.close_driver_context()


def test_unknown_alias_lists_open_ones(client, keywords):
    keywords.open_driver_context("rec", "hold", alias="real")
    with pytest.raises(Failure, match=r"no driver context 'nope' is open; open contexts: \['real'\]"):
        keywords.close_driver_context("nope")
    with pytest.raises(Failure, match="'nope'"):
        keywords.get_driver_context_value("nope")


def test_close_all_is_lifo(client, keywords, recorder):
    for name in ("a", "b", "c"):
        keywords.open_driver_context("rec", "hold", name, alias=name)
    keywords.close_all_driver_contexts()
    assert recorder.events[3:] == ["release:token-c", "release:token-b", "release:token-a"]
    assert open_contexts() == []
    keywords.close_all_driver_contexts()  # nothing open: no error


def test_close_all_closes_every_context_and_reports_failure(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "a", alias="a")
    keywords.open_driver_context("rec", "broken_exit", alias="broken")
    keywords.open_driver_context("rec", "hold", "c", alias="c")
    with pytest.raises(RuntimeError, match="exit failed"):
        keywords.close_all_driver_contexts()
    assert recorder.events[-2:] == ["release:token-c", "release:token-a"]
    assert open_contexts() == []


def test_failing_exit_fails_close_and_forgets_context(client, keywords):
    keywords.open_driver_context("rec", "broken_exit")
    with pytest.raises(RuntimeError, match="exit failed"):
        keywords.close_driver_context()
    assert open_contexts() == []


def test_failing_enter_leaves_nothing_open(client, keywords):
    with pytest.raises(RuntimeError, match="enter failed"):
        keywords.open_driver_context("rec", "broken_enter")
    assert open_contexts() == []


def test_not_a_context_manager(client, keywords):
    with pytest.raises(Failure, match="did not return a context manager"):
        keywords.open_driver_context("rec", "not_a_context")
    with pytest.raises(Failure, match="has no method 'missing'"):
        keywords.open_driver_context("rec", "missing")


def test_missing_driver_path(client, keywords):
    with pytest.raises(Failure, match="not present on this exporter"):
        keywords.open_driver_context("nope", "hold")


def test_bad_scope_fails_before_entering(client, keywords, recorder):
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.open_driver_context("rec", "hold", scope="forever")
    assert recorder.events == []


def test_contexts_close_with_their_session(recorder, keywords):
    with serve(Composite(children={"rec": recorder})) as root:
        with use_client(root, "other"):
            keywords.open_driver_context("rec", "hold", "s", session="other", scope="global")
            assert open_contexts()[0].session == "other"
        # use_client closed the session, which closed the context first
        assert recorder.events == ["acquire:s", "release:token-s"]
        assert open_contexts() == []


def test_end_run_closes_global_contexts(client, keywords, recorder):
    keywords.open_driver_context("rec", "hold", "g", scope="global")
    context.end_test()
    context.end_suite("s1")
    assert recorder.events == ["acquire:g"]
    context.end_run()
    assert recorder.events == ["acquire:g", "release:token-g"]


def test_no_session(keywords):
    with pytest.raises(Failure, match="no Jumpstarter session"):
        keywords.open_driver_context("rec", "hold", session="absent")


# ---- Collect Driver Stream ----


def test_collect_count_cancels_endless_stream(client, keywords, recorder):
    assert keywords.collect_driver_stream("rec", "ticks", 5, count=3, timeout=None) == [5, 6, 7]
    assert wait_for(lambda: "ticks:stop" in recorder.events), recorder.events


def test_collect_count_cancels_fast_stream(client, keywords, recorder):
    assert keywords.collect_driver_stream("rec", "burst", count=3) == [0, 1, 2]
    assert wait_for(lambda: "burst:stop" in recorder.events), recorder.events
    # the stream really stopped: nothing keeps it busy on the exporter
    assert keywords.collect_driver_stream("rec", "burst", count=1) == [0]


def test_collect_timeout_cancels_stream(client, keywords, recorder):
    started = time.monotonic()
    assert keywords.collect_driver_stream("rec", "slow", timeout="0.3s") == [1]
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 3, elapsed
    assert wait_for(lambda: "slow:stop" in recorder.events), recorder.events


def test_collect_timeout_before_count(client, keywords, recorder):
    assert keywords.collect_driver_stream("rec", "slow", count=5, timeout=0.2) == [1]
    assert wait_for(lambda: "slow:stop" in recorder.events)


def test_collect_finite_stream_ends(client, keywords):
    readings = keywords.collect_driver_stream("power", "read", timeout="5s")
    assert [r.voltage for r in readings] == [0.0, 5.0]
    assert isinstance(readings[0], DotDict)


def test_collect_driver_method_and_models(client, keywords):
    items = keywords.collect_driver_stream("rec", "info_stream", driver_method="models", count=1)
    assert items == [{"serial": "abc", "ports": [1, 2]}]
    assert isinstance(items[0], DotDict)


def test_collect_count_zero(client, keywords, recorder):
    assert keywords.collect_driver_stream("rec", "ticks", count=0) == []
    assert "ticks:start" not in recorder.events


def test_collect_needs_a_limit(client, keywords):
    with pytest.raises(Failure, match="needs count, timeout or both"):
        keywords.collect_driver_stream("rec", "ticks", count=None, timeout=None)
    with pytest.raises(Failure, match="count must not be negative"):
        keywords.collect_driver_stream("rec", "ticks", count=-1)


def test_collect_driver_error_is_a_failure(client, keywords):
    with pytest.raises(Failure, match="Collect Driver Stream: .*stream broke"):
        keywords.collect_driver_stream("rec", "failing", count=5)


def test_collect_unknown_method(client, keywords):
    with pytest.raises(Failure, match="Collect Driver Stream"):
        keywords.collect_driver_stream("rec", "nonexistent", count=1)


# ---- Get Driver Property ----


def test_get_property(client, keywords):
    assert keywords.get_driver_property("rec", "label") == "recorder"
    assert keywords.get_driver_property("rec", "pair") == ["host", 22]


def test_get_cached_property(client, keywords):
    RecorderClient.info_calls = 0
    first = keywords.get_driver_property("rec", "info")
    assert first == {"serial": "cached", "ports": [3]} and isinstance(first, DotDict)
    keywords.get_driver_property("rec", "info")
    assert RecorderClient.info_calls == 1


def test_get_property_errors(client, keywords):
    with pytest.raises(Failure, match="has no property 'nope'"):
        keywords.get_driver_property("rec", "nope")
    with pytest.raises(Failure, match="is a method; use Call Driver"):
        keywords.get_driver_property("rec", "not_a_context")


# ---- in a real Robot Framework run ----

SUITE = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter
Suite Setup    Open Driver Context    rec    hold    suite    alias=suite

*** Test Cases ***
Test Scope Context
    ${token}=    Open Driver Context    rec    hold    test    scope=test
    Should Be Equal    ${token}    token-test
    ${value}=    Get Driver Context Value    hold
    Should Be Equal    ${value}    token-test

Test Scope Was Closed At End Of Previous Test
    ${events}=    Call Driver    rec    get_events
    Should Contain    ${events}    release:token-test
    Should Not Contain    ${events}    release:token-suite
    Run Keyword And Expect Error    *no driver context 'hold' is open*    Get Driver Context Value    hold

Tuple Value And Named Arguments
    ${forward}=    Open Driver Context    rec    forward    port=4321    alias=fwd
    Should Be Equal    ${forward}[0]    127.0.0.1
    Should Be Equal As Integers    ${forward}[1]    4321
    Close Driver Context    fwd

Duplicate Alias Fails
    Run Keyword And Expect Error    *'suite' is already open*    Open Driver Context    rec    hold    x    alias=suite

Collect Stream
    ${ticks}=    Collect Driver Stream    rec    ticks    ${10}    count=4    timeout=None
    Length Should Be    ${ticks}    4
    Should Be Equal As Integers    ${ticks}[3]    13
    ${slow}=    Collect Driver Stream    rec    slow    timeout=200ms
    Length Should Be    ${slow}    1
    ${readings}=    Collect Driver Stream    power    read    count=1
    Should Be Equal As Numbers    ${readings[0].voltage}    0.0
    Run Keyword And Expect Error    *needs count, timeout or both*
    ...    Collect Driver Stream    rec    ticks    timeout=None

Property
    ${label}=    Get Driver Property    rec    label
    Should Be Equal    ${label}    recorder
    ${info}=    Get Driver Property    rec    info
    Should Be Equal    ${info.serial}    cached
"""


def run_suite(tmp_path: Path, text: str) -> tuple[int, dict[str, str]]:
    suite = tmp_path / "contexts.robot"
    suite.write_text(text)
    output = tmp_path / "output.xml"
    rc = robot.run(str(suite), outputdir=str(tmp_path), output=str(output), report="NONE", log="NONE", console="none")
    result = ExecutionResult(str(output))
    statuses: dict[str, str] = {}

    def visit(suite) -> None:
        for test in suite.tests:
            statuses[test.name] = test.status if test.passed else f"{test.status}: {test.message}"
        for child in suite.suites:
            visit(child)

    visit(result.suite)
    return rc, statuses


def test_robot_suite(tmp_path, recorder):
    with serve(Composite(children={"rec": recorder, "power": MockPower()})) as root, use_client(root):
        rc, statuses = run_suite(tmp_path, SUITE)
        # checked while the exporter still runs: its teardown would end the streams anyway
        assert wait_for(lambda: "ticks:stop" in recorder.events and "slow:stop" in recorder.events), recorder.events
    assert statuses == dict.fromkeys(statuses, "PASS"), statuses
    assert len(statuses) == 6
    assert rc == 0
    events = recorder.events
    # the test-scoped context closed after its test, the suite one at the end of the suite
    assert events.index("release:token-test") < events.index("ticks:start")
    assert events[-1] == "release:token-suite"
    assert open_contexts() == []
    assert sessions.opened() is None


INTERRUPTED = """
*** Settings ***
Library    jumpstarter_robot.Jumpstarter    auto_import=False

*** Test Cases ***
Collect Times Out
    [Timeout]    0.5s
    Collect Driver Stream    rec    slow    count=5    timeout=None

Enter Times Out
    [Timeout]    0.5s
    Open Driver Context    rec    hold_forever

Session Still Works
    ${events}=    Call Driver    rec    get_events
    Should Contain    ${events}    slow:stop
"""


def test_interrupted_keywords_cancel_their_portal_tasks(tmp_path, recorder):
    """An RF timeout stops the stream on the exporter and the pending enter, not only the wait for them."""
    RecorderClient.enters = []
    with serve(Composite(children={"rec": recorder})) as root, use_client(root):
        rc, statuses = run_suite(tmp_path, INTERRUPTED)
        # checked while the exporter still runs: its teardown would end them anyway
        assert wait_for(lambda: "slow:stop" in recorder.events), recorder.events
        assert wait_for(lambda: RecorderClient.enters == ["entering", "cancelled"]), RecorderClient.enters
    assert statuses["Collect Times Out"].startswith("FAIL: Test timeout 500 milliseconds exceeded"), statuses
    assert statuses["Enter Times Out"].startswith("FAIL: Test timeout"), statuses
    assert statuses["Session Still Works"] == "PASS", statuses
    assert rc == 2
    assert open_contexts() == []


# ---- Open Port Forward ----


def test_port_forward_reaches_the_driver_stream():
    import socket

    from jumpstarter_driver_network.driver import EchoNetwork

    keywords = ContextKeywords()
    with serve(Composite(children={"echo": EchoNetwork()})) as root, use_client(root):
        try:
            address = keywords.open_port_forward("echo", alias="echo")
            host, port = address.rsplit(":", 1)
            assert host == "127.0.0.1"
            with socket.create_connection((host, int(port)), timeout=5) as sock:
                sock.sendall(b"ping")
                assert sock.recv(4) == b"ping"
            assert keywords.get_driver_context_value("echo") == address
            keywords.close_driver_context("echo")
            with pytest.raises(OSError):
                socket.create_connection((host, int(port)), timeout=1).close()
        finally:
            context.end_run()


def test_port_forward_rejects_a_bad_scope(client, keywords):
    with pytest.raises(Failure, match="scope must be one of"):
        keywords.open_port_forward("rec", scope="forever")

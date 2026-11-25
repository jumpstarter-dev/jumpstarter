from __future__ import annotations

import itertools
import signal
import threading
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager

import anyio
import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell
from pydantic import BaseModel
from robot.api import Failure
from robot.libdoc import LibraryDocumentation
from robot.utils import DotDict

from . import context
from .driver_library import Driver, import_client_class
from .library import Jumpstarter
from .testing import use_client
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver as JumpstarterDriver
from jumpstarter.driver import export

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
GADGET = f"{__name__}.GadgetClient"


class Settings(BaseModel):
    level: int = 1


class GadgetClient(DriverClient):
    """A client with every member shape."""

    @property
    def label(self) -> str:
        return self.call("label")

    def numbers(self, start: int = 0) -> Generator[int, None, None]:
        yield from self.streamingcall("numbers", start)

    def apply(self, settings: Settings) -> int:
        return self.call("apply", settings.model_dump())

    @contextmanager
    def opened(self):
        yield self

    async def background(self) -> None: ...


class Gadget(JumpstarterDriver):
    @classmethod
    def client(cls) -> str:
        return GADGET

    @export
    def label(self) -> str:
        return "gadget-1"

    @export
    async def numbers(self, start: int) -> AsyncGenerator[int, None]:
        for n in range(int(start), int(start) + 100):
            yield n

    @export
    def apply(self, settings: dict) -> int:
        return settings["level"]


@pytest.fixture(scope="module")
def served():
    root = Composite(
        children={
            "dut": Composite(children={"power": MockPower()}),
            "dut2": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hello $1 $GREETING", "fail": "echo oops >&2; exit 3"}),
            "gadget": Gadget(),
        }
    )
    with serve(root) as client:
        yield client


@pytest.fixture
def tree(served):
    with use_client(served):
        yield served


def run(library: Driver, name: str, *args, **named):
    return library.run_keyword(name, list(args), named)


class TestKeywords:
    def test_power_client(self):
        library = Driver("dut.power", client=POWER)
        assert library.get_keyword_names() == [
            "Dut Power On",
            "Dut Power Off",
            "Dut Power Rescue",
            "Dut Power Cycle",
            "Dut Power Status",
            "Dut Power Read",
        ]
        assert library.get_keyword_arguments("Dut Power Cycle") == [("wait", 2)]
        assert library.get_keyword_types("Dut Power Cycle") == {"wait": int}
        assert library.get_keyword_arguments("Dut Power Read") == ["*", ("max_items", 1000)]
        assert library.get_keyword_tags("Dut Power Read") == [
            "jumpstarter",
            "driver:dut.power",
            "class:PowerClient",
            "generator",
        ]
        assert library.get_keyword_documentation("Dut Power On").startswith("Power on the device.")

    def test_prefix(self):
        assert "Main Power On" in Driver("dut.power", client=POWER, prefix="Main Power").get_keyword_names()
        assert "On" in Driver("dut.power", client=POWER, prefix="").get_keyword_names()

    def test_member_shapes(self):
        library = Driver("gadget", client=GADGET)
        names = library.get_keyword_names()
        assert "Get Gadget Label" in names
        assert "Gadget Numbers" in names
        assert "Gadget Opened" not in names  # context managers get no keyword
        assert "Gadget Background" not in names  # async members are skipped

    def test_include_exclude(self):
        assert Driver("p", client=POWER, include="on,off").get_keyword_names() == ["P On", "P Off"]
        assert "P Read" not in Driver("p", client=POWER, exclude=["read"]).get_keyword_names()

    def test_import_client_class(self):
        assert import_client_class(POWER).__name__ == "PowerClient"
        assert import_client_class("jumpstarter_driver_power.client:PowerClient").__name__ == "PowerClient"
        with pytest.raises(ValueError, match="dotted class path"):
            import_client_class("PowerClient")

    def test_libdoc_needs_no_session(self):
        doc = LibraryDocumentation(f"jumpstarter_robot.Driver::dut.power::client={POWER}::prefix=Power")
        keywords = {kw.name: kw for kw in doc.keywords}
        assert [str(arg) for arg in keywords["Power Cycle"].args] == ["wait: int = 2"]
        assert keywords["Power On"].source.endswith("client.py")


class TestCalls:
    def test_methods(self, tree):
        library = Driver("dut.power", client=POWER, prefix="Power")
        assert run(library, "Power On") is None
        assert run(library, "Power Status") == "on"
        run(library, "Power Off")
        assert tree.children["dut"].children["power"].status() == "off"

    def test_generator_returns_at_most_max_items(self, tree):
        library = Driver("gadget", client=GADGET, max_items=4)
        assert run(library, "Gadget Numbers") == [0, 1, 2, 3]
        assert run(library, "Gadget Numbers", 10, max_items=2) == [10, 11]
        readings = run(Driver("dut.power", client=POWER), "Dut Power Read", max_items="1")
        assert isinstance(readings[0], DotDict) and readings[0].voltage == 0.0

    def test_property(self, tree):
        assert run(Driver("gadget", client=GADGET), "Get Gadget Label") == "gadget-1"

    def test_pydantic_argument(self, tree):
        library = Driver("gadget", client=GADGET)
        assert run(library, "Gadget Apply", '{"level": 3}') == 3
        with pytest.raises(Failure, match="Invalid value for argument 'settings'"):
            run(library, "Gadget Apply", "not json")

    def test_class_mismatch(self, tree):
        with pytest.raises(Failure, match="correct its client argument"):
            run(Driver("dut.power", client=SHELL), "Dut Power Run Method", "x")

    def test_missing_driver(self, tree):
        with pytest.raises(Failure, match="required driver is not on exporter 'test'"):
            run(Driver("camera", client=POWER), "Camera On")

    def test_no_session(self):
        with pytest.raises(Failure, match="Dut Power On: no Jumpstarter session 'default'"):
            run(Driver("dut.power", client=POWER), "Dut Power On")


class TestShell:
    def test_configured_methods(self, tree):
        library = Driver("shell", client=SHELL, methods="hello,fail")
        assert library.get_keyword_names()[-3:] == ["Shell Run Method", "Shell Hello", "Shell Fail"]
        result = run(library, "Shell Hello", "world", GREETING="hi")
        assert result.rc == 0
        assert result.stdout.strip() == "hello world hi"

    def test_expected_rc(self, tree):
        library = Driver("shell", client=SHELL, methods="hello,fail")
        with pytest.raises(Failure, match="shell method 'fail' returned 3, expected 0"):
            run(library, "Shell Fail")
        assert run(library, "Shell Fail", expected_rc=3).rc == 3


class TestRoles:
    def test_bind_and_reset(self, tree):
        library = Driver(role="power", client=POWER)
        assert "Power On" in library.get_keyword_names()
        library.bind("dut2.power")
        run(library, "Power On")
        assert tree.children["dut2"].children["power"].status() == "on"
        library.end_test(None, None)
        assert library.path == "power"  # bindings reset after each test

    def test_use_device(self, tree):
        power = Driver(role="power", client=POWER)
        assert Jumpstarter().use_device("dut2") == ["dut2.power"]
        assert power.path == "dut2.power"


class LimitedClient(DriverClient):
    def read(self, max_items: int) -> Generator[int, None, None]:
        yield from self.streamingcall("read", max_items)


class GreedyClient(DriverClient):
    def read(self, max_items: int, keyword_max_items: int) -> Generator[int, None, None]:
        yield from self.streamingcall("read", max_items + keyword_max_items)


class Limited(JumpstarterDriver):
    active = 0
    blocking = threading.Event()

    @classmethod
    def client(cls) -> str:
        return f"{__name__}.LimitedClient"

    @export
    async def read(self, start: int) -> AsyncGenerator[int, None]:
        Limited.active += 1
        try:
            while True:
                yield start
                start += 1
                await anyio.sleep(0.01)
        finally:
            Limited.active -= 1

    @export
    async def flood(self) -> AsyncGenerator[int, None]:
        Limited.active += 1
        try:
            while True:
                yield 1
        finally:
            Limited.active -= 1

    @export
    def block(self) -> Generator[int, None, None]:
        Limited.active += 1
        try:
            yield 1
            Limited.blocking.set()
            time.sleep(2)  # on the event loop the client shares with a local exporter
            while True:
                yield 2
        finally:
            Limited.active -= 1


class TestBoundedStreams:
    @pytest.fixture
    def limited(self):
        with serve(Composite(children={"gauge": Limited()})) as client, use_client(client):
            yield client
            assert "streamingcall" not in vars(client.children["gauge"])

    def wait_idle(self):
        deadline = time.monotonic() + 5
        while Limited.active and time.monotonic() < deadline:
            time.sleep(0.02)
        assert Limited.active == 0

    def test_the_exporter_stream_ends_with_the_keyword(self, limited):
        driver = Driver("gauge", client=f"{__name__}.LimitedClient", max_items=3)
        assert [int(v) for v in driver.run_keyword("Gauge Read", [5])] == [5, 6, 7]
        self.wait_idle()

    def test_call_driver_ends_the_stream_too(self, limited, monkeypatch):
        library = Jumpstarter()
        monkeypatch.setattr(
            "jumpstarter_robot.library.context.to_robot", lambda value, **_: next(value) or value.close()
        )
        library.call_driver("gauge", "read", 1)
        self.wait_idle()

    def test_the_clients_own_max_items_keeps_its_name(self, limited):
        driver = Driver("gauge", client=f"{__name__}.LimitedClient", max_items=2)
        assert "max_items" in driver.get_keyword_arguments("Gauge Read")
        values = driver.run_keyword("Gauge Read", [], {"max_items": 7, "keyword_max_items": 4})
        assert [int(v) for v in values] == [7, 8, 9, 10]
        assert len(driver.run_keyword("Gauge Read", [], {"max_items": 7})) == 2
        self.wait_idle()

    def test_interrupted_reads_leave_no_stream(self, limited):
        """An RF timeout may land anywhere, also while a read is being scheduled."""

        class Interrupted(Exception): ...

        def interrupt(signum, frame):
            raise Interrupted

        gauge = limited.children["gauge"]
        previous = signal.signal(signal.SIGALRM, interrupt)
        try:
            for attempt in range(40):
                with pytest.raises(Interrupted), context.bounded_streams(gauge):
                    signal.setitimer(signal.ITIMER_REAL, 0.01 + attempt * 0.0007)
                    try:
                        for _ in gauge.streamingcall("flood"):
                            pass
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous)
        self.wait_idle()

    def test_interrupting_a_blocked_portal_is_bounded(self, limited, monkeypatch):
        monkeypatch.setattr(context, "CANCEL_GRACE", 0.2)
        Limited.blocking.clear()
        main = threading.get_ident()
        interrupted_at = []

        class Interrupted(Exception): ...

        def interrupt(signum, frame):
            raise Interrupted

        def interrupt_once_blocked():
            if Limited.blocking.wait(10):
                interrupted_at.append(time.monotonic())
                signal.pthread_kill(main, signal.SIGALRM)

        gauge = limited.children["gauge"]
        previous = signal.signal(signal.SIGALRM, interrupt)
        try:
            threading.Thread(target=interrupt_once_blocked, daemon=True).start()
            with pytest.raises(Interrupted), context.bounded_streams(gauge):
                for _ in itertools.islice(gauge.streamingcall("block"), 1000):
                    pass
        finally:
            signal.signal(signal.SIGALRM, previous)
        assert time.monotonic() - interrupted_at[0] < 1.5  # the driver blocks for two seconds
        self.wait_idle()  # and once it does not, its stream ends as well

    def test_interrupted_patching_is_undone(self, limited, monkeypatch):
        real = context._registered_streamingcall
        calls = []

        def interrupted(client, ending):
            if calls:
                raise KeyboardInterrupt
            calls.append(client)
            return real(client, ending)

        monkeypatch.setattr(context, "_registered_streamingcall", interrupted)
        with pytest.raises(KeyboardInterrupt), context.bounded_streams(limited):
            pass
        assert "streamingcall" not in vars(calls[0])

    def test_a_client_with_both_limit_names_keeps_them(self, limited):
        driver = Driver("gauge", client=f"{__name__}.GreedyClient", max_items=2)
        gauge = limited.children["gauge"]
        assert ("keyword_keyword_max_items", 2) in driver.get_keyword_arguments("Gauge Read")
        gauge.labels["jumpstarter.dev/client"] = f"{__name__}.GreedyClient"
        gauge.read = GreedyClient.read.__get__(gauge)
        values = driver.run_keyword(
            "Gauge Read", [], {"max_items": 1, "keyword_max_items": 2, "keyword_keyword_max_items": 3}
        )
        assert [int(v) for v in values] == [3, 4, 5]

    def test_dropped_generators_do_not_swallow_an_interruption(self, limited):
        class Interrupted(Exception): ...

        def interrupt(signum, frame):
            raise Interrupted

        gauge = limited.children["gauge"]
        previous = signal.signal(signal.SIGALRM, interrupt)
        try:
            for attempt in range(15):
                deadline = time.monotonic() + 3
                with pytest.raises(Interrupted), context.bounded_streams(gauge):
                    signal.setitimer(signal.ITIMER_REAL, 0.03 + attempt * 0.003)
                    while time.monotonic() < deadline:
                        next(gauge.streamingcall("read", 1))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        self.wait_idle()

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell
from pydantic import BaseModel
from robot.api import Failure
from robot.libdoc import LibraryDocumentation
from robot.utils import DotDict

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
        assert "Gadget Opened" not in names  # context managers go through Open Driver Context
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
        with pytest.raises(Failure, match="regenerate the client resource"):
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

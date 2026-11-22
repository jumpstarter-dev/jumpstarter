import enum
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import BaseModel

from jumpstarter_mcp import catalogue

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import mcp
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


class Mode(enum.Enum):
    FAST = "fast"
    SAFE = "safe"


class Reading(BaseModel):
    voltage: float
    current: float


class BenchClient(DriverClient):
    """A bench supply: what the tests' tools are made from."""

    def on(self) -> None:
        """Switch the output on."""
        self.call("on")

    def set_voltage(self, volts: float, mode: Mode = Mode.SAFE, *, settle: int = 0) -> str:
        """Set the output voltage.

        Longer notes that are not the first line.

        Args:
            volts: The voltage to set, in volts.
            mode (Mode): How fast to get there:
                safe ramps.
            settle: Seconds to wait after.

        Returns:
            What the supply said.
        """
        return self.call("set_voltage", volts, mode.value, settle)

    def read(self) -> Reading:
        return Reading.model_validate(self.call("read"))

    @property
    def serial_number(self) -> str:
        return self.call("serial_number")

    @property
    def extra(self) -> DriverClient:
        """The child driver, by name: a driver of the lease itself, not a tool of this one."""
        return self.children["extra"]

    def should_be_on(self) -> None:
        """Fail unless the output is on."""

    def samples(self, start: int = 0) -> Generator[int, None, None]:
        yield from self.streamingcall("samples", start)

    def upload(self, path: Path, target: object = None) -> str:
        return f"{type(path).__name__}:{path}"

    @mcp.tool("wipe", description="Erase the stored profile.", destructive=True)
    def erase_profile(self) -> str:
        return self.call("erase")

    @mcp.not_tool
    def raw(self) -> str:
        return "never a tool"

    @contextmanager
    def session(self):
        yield self

    async def background(self) -> None: ...

    def _private(self) -> None: ...


@mcp.toolset
class CuratedClient(DriverClient):
    """Only what its author marked."""

    @mcp.tool(read_only=True)
    def state(self) -> str:
        return "ok"

    def everything_else(self) -> None: ...


class Bench(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_mcp.catalogue_test.BenchClient"

    @export
    def on(self) -> None: ...

    @export
    def set_voltage(self, volts: float, mode: str, settle: int) -> str:
        return f"{volts:g} V {mode} after {int(settle)}"

    @export
    def read(self) -> dict:
        return {"voltage": 5.0, "current": 0.25}

    @export
    def serial_number(self) -> str:
        return "SN-1"

    @export
    def samples(self, start: int):
        yield from range(int(start), int(start) + 1000)

    @export
    def erase(self) -> str:
        return "erased"


class Curated(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_mcp.catalogue_test.CuratedClient"


@pytest.fixture
def bench():
    with serve(Bench(children={"extra": Curated()})) as client:
        yield client


def by_name(client) -> dict[str, catalogue.Command]:
    return {command.qualified: command for command in catalogue.commands(client)}


def test_a_drivers_public_members_are_its_commands(bench):
    assert sorted(by_name(bench)) == [
        "extra.state",
        "on",
        "read",
        "samples",
        "serial_number",
        "set_voltage",
        "should_be_on",
        "upload",
        "wipe",
    ]


def test_a_command_has_a_schema_made_from_the_methods_signature(bench):
    command = by_name(bench)["set_voltage"]
    assert command.description.startswith("Set the output voltage.")
    properties = command.schema["properties"]
    assert properties["volts"]["type"] == "number"
    # the documentation of the method's arguments is the documentation of the tool's
    assert properties["volts"]["description"] == "The voltage to set, in volts."
    assert properties["mode"]["description"] == "How fast to get there: safe ramps."
    assert properties["settle"]["description"] == "Seconds to wait after."
    assert properties["settle"]["default"] == 0 and properties["settle"]["type"] == "integer"
    assert command.schema["required"] == ["volts"]
    assert command.schema["additionalProperties"] is False
    # an enumeration is a choice among its values
    assert command.schema["$defs"]["Mode"]["enum"] == ["fast", "safe"]


def test_what_a_command_does_to_the_device_is_said(bench):
    commands = by_name(bench)
    assert (commands["read"].read_only, commands["serial_number"].read_only, commands["samples"].read_only) == (
        True,
        True,
        True,
    )
    assert (commands["on"].read_only, commands["on"].destructive) == (False, False)
    assert commands["should_be_on"].read_only is True  # it checks, and changes nothing
    assert (commands["wipe"].member, commands["wipe"].destructive) == ("erase_profile", True)
    assert commands["wipe"].description == "Erase the stored profile."
    assert commands["extra.state"].read_only is True


def test_a_tool_is_named_by_its_driver_and_its_command_never_by_its_lease(bench):
    assert by_name(bench)["extra.state"].tool == "extra_state"
    assert catalogue.tool_name(("dut-link", "Power.1"), "on") == "dut_link_power_1_on"
    long = catalogue.tool_name(tuple(["section"] * 12), "read_everything")
    assert len(long) <= catalogue.MAX_TOOL_NAME and long.endswith("read_everything")
    assert long != catalogue.tool_name(tuple(["sectiom"] * 12), "read_everything")


def test_a_command_is_called_with_checked_arguments_and_answers_plain_json(bench):
    node, command = catalogue.find_tool(bench, "set_voltage")
    assert catalogue.invoke(node, command, {"volts": "3.3", "mode": "fast", "settle": 2}) == "3.3 V fast after 2"
    assert catalogue.invoke(node, command, {"volts": 5}) == "5 V safe after 0"
    assert catalogue.invoke(*catalogue.find_tool(bench, "read")) == {"voltage": 5.0, "current": 0.25}
    assert catalogue.invoke(*catalogue.find_tool(bench, "serial_number")) == "SN-1"
    assert catalogue.invoke(*catalogue.find_tool(bench, "wipe")) == "erased"
    assert catalogue.invoke(*catalogue.find_tool(bench, "extra_state")) == "ok"


def test_a_generator_is_read_up_to_a_number_of_items(bench):
    node, command = catalogue.find_tool(bench, "samples")
    assert catalogue.invoke(node, command, {"start": 5, "max_items": 3}) == [5, 6, 7]
    assert len(catalogue.invoke(node, command, {})) == catalogue.DEFAULT_MAX_ITEMS


def test_an_argument_json_cannot_describe_is_passed_as_it_comes(bench):
    node, command = catalogue.find_tool(bench, "upload")
    assert command.schema["properties"]["path"]["format"] == "path"
    assert catalogue.invoke(node, command, {"path": "/tmp/image.bin"}).endswith(":/tmp/image.bin")


def test_wrong_arguments_and_wrong_names_are_said_before_the_device_is_touched(bench):
    node, command = catalogue.find_tool(bench, "set_voltage")
    with pytest.raises(catalogue.CommandError, match="volts"):
        catalogue.invoke(node, command, {})
    with pytest.raises(catalogue.CommandError, match="unknown"):
        catalogue.invoke(node, command, {"volts": 1, "unknown": True})
    with pytest.raises(catalogue.CommandError, match="Did you mean: set_voltage"):
        catalogue.find_tool(bench, "voltage")
    with pytest.raises(catalogue.CommandError, match="jmp_tools"):
        catalogue.find_tool(bench, "nothing_like_it")
    with pytest.raises(catalogue.CommandError, match="has no driver 'missing'"):
        catalogue.resolve(bench, ("missing",))


def test_the_drivers_of_a_lease_are_listed_with_their_commands(bench):
    listed = {entry["driver"]: entry for entry in catalogue.drivers(bench)}
    assert listed[""]["class"].endswith("BenchClient")
    assert listed[""]["description"] == "A bench supply: what the tests' tools are made from."
    assert listed["extra"]["commands"] == ["state"]


def test_arguments_are_documented_from_either_kind_of_docstring():
    fields = "Do.\n\n:param x: The first.\n    continued\n:param int y: The second.\n:returns: z"
    assert catalogue.documented(fields) == {"x": "The first. continued", "y": "The second."}
    section = "Do.\n\nArgs:\n    x: One.\n    **kwargs: The rest.\n\nRaises:\n    ValueError: no"
    assert catalogue.documented(section) == {"x": "One.", "kwargs": "The rest."}
    assert catalogue.documented(None) == {} and catalogue.documented("No arguments said.") == {}

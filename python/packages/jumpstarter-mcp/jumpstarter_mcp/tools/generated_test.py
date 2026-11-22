from types import SimpleNamespace

import pytest

from jumpstarter_mcp.catalogue_test import Bench, Curated
from jumpstarter_mcp.tools import generated

from jumpstarter.common.utils import serve


@pytest.fixture
def manager():
    with serve(Bench(children={"extra": Curated()})) as first, serve(Bench()) as second:
        connections = {
            "one": SimpleNamespace(id="one", exporter_name="bench-1", lease_name="lease-1", client=first),
            "two": SimpleNamespace(id="two", exporter_name="bench-2", lease_name="lease-2", client=second),
        }
        yield SimpleNamespace(get_connection=connections.__getitem__)


@pytest.mark.anyio
async def test_a_lease_lists_its_drivers_and_their_tools_shortly(manager):
    listed = await generated.list_tools(manager, "one")
    assert (listed["connection_id"], listed["exporter"]) == ("one", "bench-1")
    by_driver = {entry["driver"]: entry["tools"] for entry in listed["drivers"]}
    assert by_driver["extra"] == {"extra_state": ""}
    assert by_driver[""]["set_voltage"] == "Set the output voltage."
    assert "properties" not in str(listed)  # no schemas until a driver is asked for


@pytest.mark.anyio
async def test_one_driver_lists_its_tools_with_their_arguments(manager):
    listed = await generated.list_tools(manager, "one", "extra")
    assert listed["tools"] == [
        {
            "tool": "extra_state",
            "description": "",
            "kind": "method",
            "read_only": True,
            "destructive": False,
            "arguments": {"additionalProperties": False, "properties": {}, "type": "object"},
            "call_with": "jmp_read",
        }
    ]
    assert "has no driver" in (await generated.list_tools(manager, "two", "extra"))["error"]


@pytest.mark.anyio
async def test_a_call_names_its_lease_and_two_leases_have_the_same_tools(manager):
    one = await generated.call_tool(manager, "one", "set_voltage", {"volts": 3.3})
    two = await generated.call_tool(manager, "two", "set_voltage", {"volts": 12, "mode": "fast"})
    assert (one["exporter"], one["result"]) == ("bench-1", "3.3 V safe after 0")
    assert (two["exporter"], two["result"]) == ("bench-2", "12 V fast after 0")
    # what only one of them has is said for the other, with which lease it was
    missing = await generated.call_tool(manager, "two", "extra_state")
    assert missing["connection_id"] == "two" and "no tool 'extra_state'" in missing["error"]


@pytest.mark.anyio
async def test_reading_refuses_what_changes_the_device(manager):
    assert (await generated.call_tool(manager, "one", "read", reads_only=True))["result"]["voltage"] == 5.0
    refused = await generated.call_tool(manager, "one", "wipe", reads_only=True)
    assert "jmp_call" in refused["error"]
    assert (await generated.call_tool(manager, "one", "wipe"))["result"] == "erased"


@pytest.mark.anyio
async def test_wrong_arguments_are_an_answer_not_a_failure(manager):
    wrong = await generated.call_tool(manager, "one", "set_voltage", {"volts": "a lot"})
    assert "volts" in wrong["error"] and wrong["connection_id"] == "one"

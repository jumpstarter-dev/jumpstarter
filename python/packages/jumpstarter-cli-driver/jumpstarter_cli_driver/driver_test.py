import json
from dataclasses import dataclass
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from . import driver


@dataclass
class FakeDist:
    name: str
    version: str


@dataclass
class FakeEntryPoint:
    """Stands in for an installed driver's entry point.

    The tests patch these in rather than reading the environment: which drivers
    happen to be installed is not something a CLI test should depend on, and an
    environment with none exercises nothing at all.
    """

    name: str
    value: str
    dist: FakeDist | None


ENTRY_POINTS = [
    FakeEntryPoint(
        name="power",
        value="jumpstarter_driver_power.driver:MockPower",
        dist=FakeDist(name="jumpstarter-driver-power", version="1.2.3"),
    ),
    # A driver whose distribution cannot be resolved: package and version are
    # unknown, and the row still has to render.
    FakeEntryPoint(name="orphan", value="somewhere.driver:Orphan", dist=None),
]


@pytest.fixture
def installed_drivers():
    with patch("jumpstarter_cli_driver.driver.entry_points", return_value=ENTRY_POINTS):
        yield


def test_list_drivers_table(installed_drivers):
    result = CliRunner().invoke(driver, ["list"])
    assert result.exit_code == 0
    # The name and the dotted type, one row each.
    assert "power" in result.output
    assert "jumpstarter_driver_power.driver.MockPower" in result.output
    assert "orphan" in result.output


def test_list_drivers_json(installed_drivers):
    result = CliRunner().invoke(driver, ["list", "-o", "json"])
    assert result.exit_code == 0
    entries = json.loads(result.output)["drivers"]
    assert entries[0] == {
        "name": "power",
        "type": "jumpstarter_driver_power.driver.MockPower",
        "package": "jumpstarter-driver-power",
        "version": "1.2.3",
    }
    # An unresolvable distribution reports null rather than guessing.
    assert entries[1]["package"] is None
    assert entries[1]["version"] is None


def test_list_drivers_yaml(installed_drivers):
    result = CliRunner().invoke(driver, ["list", "-o", "yaml"])
    assert result.exit_code == 0
    entries = yaml.safe_load(result.output)["drivers"]
    assert [entry["name"] for entry in entries] == ["power", "orphan"]


def test_list_drivers_name(installed_drivers):
    result = CliRunner().invoke(driver, ["list", "-o", "name"])
    assert result.exit_code == 0
    assert result.output.split() == ["power", "orphan"]


def test_list_drivers_says_so_when_there_are_none():
    with patch("jumpstarter_cli_driver.driver.entry_points", return_value=[]):
        result = CliRunner().invoke(driver, ["list"])
    assert result.exit_code == 0
    assert "No drivers found." in result.output


def test_list_drivers_empty_json_is_still_valid_json():
    """A consumer parsing the output must not have to special-case "none"."""
    with patch("jumpstarter_cli_driver.driver.entry_points", return_value=[]):
        result = CliRunner().invoke(driver, ["list", "-o", "json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"drivers": []}


def _schema_payload(output: str) -> dict:
    # Importing every driver can make third-party libraries log; the JSON
    # document starts at the first brace.
    return json.loads(output[output.index("{") :])


def test_driver_schema_json():
    result = CliRunner().invoke(driver, ["schema", "-o", "json"])
    assert result.exit_code == 0
    drivers = {entry["name"]: entry for entry in _schema_payload(result.output)["drivers"]}

    # A driver with typed config keys reports them with their JSON types.
    tcp = drivers["TcpNetwork"]
    assert tcp["type"] == "jumpstarter_driver_network.driver.TcpNetwork"
    assert tcp["properties"]["host"]["type"] == "string"
    assert tcp["properties"]["port"]["type"] == "integer"
    assert set(tcp["required"]) == {"host", "port"}
    assert tcp["error"] is None

    # Fields shared by every Driver are not part of a driver's own config.
    for entry in drivers.values():
        assert "uuid" not in entry["properties"]
        assert "children" not in entry["properties"]
        assert "log_level" not in entry["properties"]


def test_driver_schema_recovers_keys_when_schema_generation_fails():
    result = CliRunner().invoke(driver, ["schema", "-o", "json"])
    assert result.exit_code == 0
    failed = [e for e in _schema_payload(result.output)["drivers"] if e["error"]]
    # Drivers pydantic cannot model still report their config keys, recovered
    # from the dataclass fields.
    assert all(entry["properties"] for entry in failed if entry["name"] == "Qemu")


def test_driver_schema_filters_by_name_and_type():
    runner = CliRunner()

    by_name = runner.invoke(driver, ["schema", "MockPower", "-o", "json"])
    assert by_name.exit_code == 0
    assert [d["name"] for d in _schema_payload(by_name.output)["drivers"]] == ["MockPower"]

    by_type = runner.invoke(
        driver, ["schema", "jumpstarter_driver_power.driver.MockPower", "-o", "json"]
    )
    assert by_type.exit_code == 0
    assert [d["name"] for d in _schema_payload(by_type.output)["drivers"]] == ["MockPower"]


def test_driver_schema_unknown_name_errors():
    result = CliRunner().invoke(driver, ["schema", "NoSuchDriver", "-o", "json"])
    assert result.exit_code != 0
    assert "No installed driver matches" in result.output


def test_driver_schema_includes_referenced_defs():
    result = CliRunner().invoke(driver, ["schema", "Xcp", "-o", "json"])
    assert result.exit_code == 0
    entry = _schema_payload(result.output)["drivers"][0]

    # Properties may reference nested models and enums; the definitions they
    # point at have to travel with them, or the refs dangle for consumers.
    refs = {
        value["$ref"].removeprefix("#/$defs/")
        for value in entry["properties"].values()
        if isinstance(value, dict) and "$ref" in value
    }
    assert refs
    assert refs <= set(entry["defs"])

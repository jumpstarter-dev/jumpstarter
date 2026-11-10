"""Driver-schema discovery is independent of optional installed drivers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import yaml
from click.testing import CliRunner
from pydantic import BaseModel

from . import driver
from jumpstarter.driver import Driver


class Mode(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class Endpoint(BaseModel):
    mode: Mode = Mode.TCP


@dataclass(kw_only=True)
class ConfigDriver(Driver):
    """A driver with typed configuration, without hardware access."""

    host: str
    port: int
    endpoint: Endpoint = field(default_factory=Endpoint)
    runtime_state: int = field(default=0, init=False)
    # Non-pydantic runtime handle, like Qemu's TemporaryDirectory. It must not
    # break schema generation even though pydantic cannot model it.
    runtime_dir: TemporaryDirectory = field(default_factory=TemporaryDirectory, init=False)

    @classmethod
    def client(cls):
        return "example.client.ConfigClient"

    def __post_init__(self):
        raise AssertionError("Schema discovery must not instantiate a driver")


@dataclass(kw_only=True)
class RecursiveDriver(Driver):
    """A self-referencing config, without runtime fields."""

    host: str
    port: int
    peer: "RecursiveDriver | None" = None

    @classmethod
    def client(cls):
        return "example.client.ConfigClient"


@dataclass(kw_only=True)
class OpaqueDriver(Driver):
    """A driver with one constructor field pydantic cannot model."""

    host: str
    tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory)

    @classmethod
    def client(cls):
        return "example.client.OpaqueClient"


def entry_point(name="Config", cls=ConfigDriver):
    """An entry point whose load can be observed or made to fail."""
    return SimpleNamespace(
        name=name,
        value=f"{cls.__module__}:{cls.__qualname__}",
        dist=SimpleNamespace(name="example-driver", version="1.2.3"),
        load=Mock(return_value=cls),
    )


@pytest.fixture
def installed():
    entry = entry_point()
    with patch("jumpstarter_cli_driver.driver.entry_points", return_value=[entry]) as discover:
        yield entry, discover


def schema_json(*args):
    """Parse stdout directly: discovery chatter must never prefix the JSON."""
    result = CliRunner().invoke(driver, ["schema", *args, "-o", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), result


def test_typed_schema_and_metadata(installed):
    entry, discover = installed
    data, _ = schema_json()
    schema = data["drivers"][0]
    assert schema["name"] == "Config"
    assert schema["type"] == entry.value.replace(":", ".")
    assert schema["client"] == "example.client.ConfigClient"
    assert schema["package"] == "example-driver"
    assert schema["version"] == "1.2.3"
    assert schema["description"] == ConfigDriver.__doc__
    assert schema["properties"]["host"]["type"] == "string"
    assert schema["properties"]["port"]["type"] == "integer"
    assert set(schema["required"]) == {"host", "port"}
    assert schema["error"] is None
    assert set(schema["properties"]) == {"host", "port", "endpoint"}
    discover.assert_called_once_with(group="jumpstarter.drivers")
    entry.load.assert_called_once_with()
    # Nested models and enums keep resolvable references.
    assert schema["properties"]["endpoint"]["$ref"] == "#/$defs/Endpoint"
    assert schema["defs"]["Endpoint"]["properties"]["mode"]["$ref"] == "#/$defs/Mode"
    assert schema["defs"]["Mode"]["enum"] == ["tcp", "udp"]


def test_recursive_root_schema(installed):
    entry, _ = installed
    entry.load.return_value = RecursiveDriver
    schema = schema_json()[0]["drivers"][0]
    assert schema["properties"]["host"]["type"] == "string"
    assert set(schema["required"]) == {"host", "port"}
    assert schema["properties"]["peer"]["anyOf"][0]["$ref"] == "#/$defs/RecursiveDriver"
    assert "RecursiveDriver" in schema["defs"]


def test_unmodelable_field_degrades_only_itself(installed):
    entry, _ = installed
    entry.load.return_value = OpaqueDriver
    schema = schema_json()[0]["drivers"][0]
    assert schema["error"] is not None
    # The rest of the schema keeps its types.
    assert schema["properties"]["host"]["type"] == "string"
    # Only the offending field is degraded.
    assert "type" not in schema["properties"]["tempdir"]
    assert set(schema["properties"]) == {"host", "tempdir"}
    assert set(schema["required"]) == {"host"}


def test_table_shows_recovered_keys_when_schema_fails(installed):
    entry, _ = installed
    entry.load.return_value = OpaqueDriver
    result = CliRunner().invoke(driver, ["schema"])
    assert result.exit_code == 0
    assert "tempdir" in result.stdout
    assert "Unable to generate" not in result.stdout


def test_non_class_entry_point_is_reported(installed):
    entry, _ = installed
    entry.load.return_value = "not a driver"
    schema = schema_json()[0]["drivers"][0]
    assert schema["error"].startswith("Entry point did not resolve")
    assert schema["properties"] == {}


def test_broken_import_does_not_hide_other_drivers(installed):
    good, discover = installed
    broken = entry_point("Broken")
    broken.load.side_effect = ImportError("missing optional dependency")
    broken.dist = None
    discover.return_value = [good, broken]
    schemas = schema_json()[0]["drivers"]
    assert [s["name"] for s in schemas] == ["Broken", "Config"]
    assert schemas[0]["error"] == "ImportError: missing optional dependency"
    assert schemas[0]["properties"] == {}
    assert schemas[0]["package"] is None
    assert schemas[0]["version"] is None
    assert schemas[1]["error"] is None


def test_discovery_output_goes_to_stderr(installed):
    entry, _ = installed

    def noisy_import():
        print("driver import chatter {not JSON}")
        return ConfigDriver

    entry.load.side_effect = noisy_import
    _, result = schema_json()
    assert "driver import chatter" in result.stderr
    assert "driver import chatter" not in result.stdout


@pytest.mark.parametrize("selector", ["name", "type"])
def test_filter_loads_only_selected_drivers(installed, selector):
    entry, discover = installed
    other = entry_point("Other")
    other.value = "other.driver:Other"
    discover.return_value = [other, entry]
    name = entry.name if selector == "name" else entry.value.replace(":", ".")
    assert [s["name"] for s in schema_json(name)[0]["drivers"]] == ["Config"]
    other.load.assert_not_called()


@pytest.mark.parametrize("names", [["Missing"], ["Config", "Missing"]])
def test_unknown_filters_fail_before_loading(installed, names):
    entry, _ = installed
    result = CliRunner().invoke(driver, ["schema", *names, "-o", "json"])
    assert result.exit_code != 0
    assert "No installed driver matches: Missing" in result.stderr
    assert result.stdout == ""
    entry.load.assert_not_called()


@pytest.mark.parametrize("output", ["json", "yaml", "name", None])
def test_empty_discovery(installed, output):
    _, discover = installed
    discover.return_value = []
    result = CliRunner().invoke(driver, ["schema", *(["-o", output] if output else [])])
    assert result.exit_code == 0
    if output == "json":
        assert json.loads(result.stdout) == {"drivers": []}
    elif output == "yaml":
        assert yaml.safe_load(result.stdout) == {"drivers": []}
    elif output == "name":
        assert result.stdout == ""
    else:
        assert "No drivers found." in result.stdout


@pytest.mark.parametrize("output", ["yaml", "name", None])
def test_output_formats(installed, output):
    result = CliRunner().invoke(driver, ["schema", *(["-o", output] if output else [])])
    assert result.exit_code == 0
    if output == "yaml":
        assert yaml.safe_load(result.stdout)["drivers"][0]["properties"]["host"]["type"] == "string"
    elif output == "name":
        assert result.stdout.strip() == "Config"
    else:
        assert "CONFIG KEYS" in result.stdout
        assert "Config" in result.stdout
        assert "host" in result.stdout

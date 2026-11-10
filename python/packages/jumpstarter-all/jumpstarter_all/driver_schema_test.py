"""Integration check for `jmp driver schema` across every installed driver.

This runs in the jumpstarter-all environment, where all in-tree drivers are
installed, so it exercises the real entry points instead of fixtures.
"""

import dataclasses
import json
import sys
import typing
from importlib.metadata import entry_points

import pytest
from click.testing import CliRunner
from jumpstarter_cli_driver import driver as driver_cli
from pydantic import TypeAdapter

from jumpstarter.driver import Driver

BASE_FIELDS = {field.name for field in dataclasses.fields(Driver)}


def config_fields(cls):
    """The driver's own constructor fields, mirroring what the command reports."""
    return [field for field in dataclasses.fields(cls) if field.name not in BASE_FIELDS and field.init]


def installed_drivers():
    for entry_point in sorted(entry_points(group="jumpstarter.drivers"), key=lambda ep: ep.name):
        try:
            cls = entry_point.load()
        except Exception:
            # Optional dependencies are environment-specific; load failures are
            # reported per entry and covered by the schema_test fixtures.
            continue
        if isinstance(cls, type) and dataclasses.is_dataclass(cls):
            yield entry_point, cls


@pytest.fixture(scope="module")
def schemas():
    result = CliRunner().invoke(driver_cli, ["schema", "-o", "json"])
    assert result.exit_code == 0, result.output
    # Parsing stdout directly also asserts that nothing pollutes the document.
    return {entry["name"]: entry for entry in json.loads(result.stdout)["drivers"]}


def test_every_installed_driver_is_reported(schemas):
    installed = {entry_point.name for entry_point in entry_points(group="jumpstarter.drivers")}
    assert set(schemas) == installed


def test_reported_keys_are_constructor_fields(schemas):
    for entry_point, cls in installed_drivers():
        expected = {field.name for field in config_fields(cls)}
        assert set(schemas[entry_point.name]["properties"]) == expected, entry_point.name


def test_runtime_fields_do_not_break_schema(schemas):
    """A driver whose constructor fields are all modelable must get a full schema.

    Runtime-only fields (init=False, or handles like TemporaryDirectory) are not
    user config, so they must not turn the whole entry into a schema error.
    """
    for entry_point, cls in installed_drivers():
        fields = config_fields(cls)
        if not fields:
            continue
        try:
            hints = typing.get_type_hints(cls, include_extras=True)
        except Exception:
            # gpiod is declared Linux-only and driver.py binds `gpiod = None`
            # when it is missing, so resolving the excluded
            # `_chip: gpiod.Chip` / `_line: gpiod.LineRequest` annotations fails
            # on macOS. Skip only there; on Linux the hints resolve (gpiod is
            # installed) and a failure here is real, so surface it.
            if sys.platform.startswith("linux"):
                raise
            continue
        for field in fields:
            try:
                TypeAdapter(hints.get(field.name, field.type)).json_schema()
            except Exception:
                break  # this driver is allowed to report a schema error
        else:
            error = schemas[entry_point.name]["error"]
            assert error is None, f"{entry_point.name}: {error}"


def test_no_unreferenced_base_definitions(schemas):
    for name, schema in schemas.items():
        assert "Driver" not in schema["defs"], name


def test_qemu_keeps_types_and_definitions(schemas):
    if "Qemu" not in schemas:
        pytest.skip("qemu driver not installed")
    qemu = schemas["Qemu"]
    assert qemu["error"] is None
    assert qemu["properties"]["hostfwd"]["additionalProperties"]["$ref"] == "#/$defs/Hostfwd"
    assert "Hostfwd" in qemu["defs"]


@pytest.mark.parametrize("name", ["DigitalInput", "DigitalOutput", "PowerSwitch"])
def test_gpio_drivers_exclude_runtime_fields(schemas, name):
    if name not in schemas:
        pytest.skip(f"{name} not installed")
    schema = schemas[name]
    assert schema["error"] is None
    assert "_chip" not in schema["properties"]
    assert "_line" not in schema["properties"]

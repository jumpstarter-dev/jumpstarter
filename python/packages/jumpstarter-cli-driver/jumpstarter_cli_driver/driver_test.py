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

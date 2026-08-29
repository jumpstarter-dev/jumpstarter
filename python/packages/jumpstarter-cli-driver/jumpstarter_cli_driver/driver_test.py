import json

from click.testing import CliRunner

from . import driver


def test_list_drivers():
    runner = CliRunner()

    result = runner.invoke(
        driver,
        ["list"],
    )
    assert result.exit_code == 0


def test_list_drivers_json():
    runner = CliRunner()

    result = runner.invoke(
        driver,
        ["list", "-o", "json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "drivers" in data
    for entry in data["drivers"]:
        assert entry["name"]
        assert entry["type"]


def test_list_drivers_name():
    runner = CliRunner()

    result = runner.invoke(
        driver,
        ["list", "-o", "name"],
    )
    assert result.exit_code == 0

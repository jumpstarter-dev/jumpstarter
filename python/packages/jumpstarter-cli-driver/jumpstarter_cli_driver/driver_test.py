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

"""Round-trip tests for the example proto-first driver — the Python siblings of the Rust crate's
``round_trip_tests``/``custom_cycle_and_cli_through_harness`` and the JVM's ``PowerNativeIT``.

``serve(ExamplePower())`` resolves the CUSTOM client from the driver's ``@driver(client=...)``
label, and every call travels the native proto-bytes seam: typed client encode →
``forward_unary``/``forward_server_stream`` → the generated-base impl — no JSON, no hand-written
marshalling anywhere in this package.
"""

import pytest

# The generated package is a build artifact (the jumpstarter_codegen build hook); regenerate
# with `make codegen` (python/) or `uv run python -m hatch_pin_jumpstarter.codegen .` here.
pytest.importorskip(
    "jumpstarter_driver_power_example._generated.power_client",
    reason="generated modules missing — run the codegen build hook",
)

from click.testing import CliRunner  # noqa: E402

from jumpstarter_driver_power_example._generated.power_models import PowerReading  # noqa: E402
from jumpstarter_driver_power_example.client import CyclingPowerClient  # noqa: E402
from jumpstarter_driver_power_example.driver import ON_CURRENT, ON_VOLTAGE, READINGS, ExamplePower  # noqa: E402

from jumpstarter.common.utils import serve  # noqa: E402


def test_on_off_read_round_trip():
    with serve(ExamplePower()) as client:
        # @driver(client=...) on the driver resolves to the CUSTOM client subclass.
        assert isinstance(client, CyclingPowerClient), type(client)

        # Initially off: read streams zero readings (proving the call reached the driver and back).
        assert client.read_voltages() == [0.0] * READINGS

        client.on()
        assert list(client.read()) == [PowerReading(voltage=ON_VOLTAGE, current=ON_CURRENT)] * READINGS

        client.off()
        assert client.read_voltages() == [0.0] * READINGS


def test_custom_cycle_ends_powered_on():
    with serve(ExamplePower()) as client:
        assert client.read_voltages()[0] == 0.0
        client.cycle(wait=0.01)  # client-side composition of off + on
        assert client.read_voltages() == [ON_VOLTAGE] * READINGS


def test_cli_drives_the_typed_client():
    with serve(ExamplePower()) as client:
        runner = CliRunner()
        result = runner.invoke(client.cli(), ["cycle", "--wait", "0"])
        assert result.exit_code == 0, result.output
        assert client.read_voltages() == [ON_VOLTAGE] * READINGS

        result = runner.invoke(client.cli(), ["read"])
        assert result.exit_code == 0, result.output
        assert f"voltage={ON_VOLTAGE} current={ON_CURRENT}" in result.output


def test_example_advertises_the_committed_contract():
    """The introspected descriptor matches the committed proto's contract — same package, service,
    and methods — so the generated-base impl and the .proto stay in lockstep."""
    from jumpstarter.driver.proto_marshal import build_marshaller

    marshaller = build_marshaller(ExamplePower())
    assert marshaller is not None, "the example must have a native surface"
    assert marshaller.service_full_name == "jumpstarter.interfaces.power.v1.PowerInterface"
    assert sorted(spec.grpc_path.rsplit("/", 1)[1] for spec in marshaller.methods.values()) == [
        "Off",
        "On",
        "Read",
    ]

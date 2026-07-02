"""End-to-end pilot test for the GENERATED proto-first authoring surface.

Proves the full loop the migration targets: a driver implementing the **generated** interface base
is served by the generic native host, and the **generated** typed client (resolved from the
driver's client label) drives it over the native proto-bytes seam — encode → forward_unary/
forward_server_stream → decode — returning native dataclasses. No JSON, no hand-written client.
"""

import pytest

# The generated package is a build artifact (the jumpstarter_codegen build hook); regenerate
# with `make codegen` (python/) or `uv run python -m hatch_pin_jumpstarter.codegen .` here.
pytest.importorskip(
    "jumpstarter_driver_power._generated.power_client",
    reason="generated modules missing — run the codegen build hook",
)

from jumpstarter_driver_power._generated.power_client import PowerClient  # noqa: E402
from jumpstarter_driver_power._generated.power_models import PowerReading  # noqa: E402
from jumpstarter_driver_power.driver_native import NativeMockPower  # noqa: E402

from jumpstarter.common.utils import serve  # noqa: E402


def test_generated_client_drives_native_power_end_to_end():
    with serve(NativeMockPower()) as client:
        # The client label on the generated base resolves to the GENERATED typed client.
        assert isinstance(client, PowerClient), type(client)

        client.on()
        readings = list(client.read())
        assert readings == [
            PowerReading(voltage=5.0, current=1.0),
            PowerReading(voltage=5.0, current=2.0),
        ], readings

        client.off()
        assert [r.voltage for r in client.read()] == [0.0, 0.0]


def test_native_pilot_advertises_the_committed_contract():
    """The pilot's introspected descriptor matches the committed proto's contract — same package,
    service, and methods — so the generated-base path and the .proto stay in lockstep."""
    from jumpstarter.driver.proto_marshal import build_marshaller

    marshaller = build_marshaller(NativeMockPower())
    assert marshaller is not None, "pilot must have a native surface"
    assert marshaller.service_full_name == "jumpstarter.interfaces.power.v1.PowerInterface"
    assert sorted(spec.grpc_path.rsplit("/", 1)[1] for spec in marshaller.methods.values()) == [
        "Off",
        "On",
        "Read",
    ]

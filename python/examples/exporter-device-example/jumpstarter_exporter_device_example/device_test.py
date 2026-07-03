"""End-to-end test for the GENERATED typed device wrapper.

The exporter tree is instantiated FROM THE SAME committed ``exporter.yaml`` the wrapper was
generated from (``DriverHostFactory.from_yaml`` + ``LocalSession``), so the config and the
served tree cannot drift. The wrapper then binds every node to its codegen-chosen client:
``dut.power`` → the custom ``CyclingPowerClient`` (package-local registry entry), and
``dut.backup_power`` → this package's own GENERATED ``PowerClient`` — REBOUND over whatever
class the runtime resolved from the node's client label.
"""

from contextlib import ExitStack
from pathlib import Path

import pytest

# The generated package is a build artifact (the jumpstarter_codegen build hook); regenerate
# with `make codegen` (python/) or `uv run python -m hatch_pin_jumpstarter.codegen .` here.
pytest.importorskip(
    "jumpstarter_exporter_device_example._generated.device",
    reason="generated modules missing — run the codegen build hook",
)

from anyio.from_thread import start_blocking_portal  # noqa: E402
from jumpstarter_driver_power_example.client import CyclingPowerClient  # noqa: E402
from jumpstarter_driver_power_example.driver import ON_VOLTAGE  # noqa: E402

from jumpstarter_exporter_device_example._generated.device import ExampleRigDevice  # noqa: E402
from jumpstarter_exporter_device_example._generated.power_client import PowerClient  # noqa: E402
from jumpstarter_exporter_device_example._generated.power_models import PowerReading  # noqa: E402

EXPORTER_YAML = (Path(__file__).parent.parent / "exporter.yaml").read_text()


@pytest.fixture
def device():
    """Serve the committed exporter.yaml in-process and bind the generated device wrapper."""
    from jumpstarter.client.client import client_from_session
    from jumpstarter.exporter.host import DriverHostFactory, LocalSession

    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            host = DriverHostFactory.from_yaml(EXPORTER_YAML).new_host()
            session = LocalSession(host)
            root = portal.call(client_from_session, session, portal, stack, [], True)
            yield ExampleRigDevice(root)


def test_custom_client_binds_from_the_package_registry(device):
    power = device.dut.power
    assert isinstance(power, CyclingPowerClient), type(power)
    assert power.read_voltages() == [0.0, 0.0, 0.0]
    power.cycle(wait=0.01)  # the custom client-side extension method
    assert power.read_voltages() == [ON_VOLTAGE] * 3
    power.off()


def test_generated_client_binds_and_rebinds_over_the_advertised_label(device):
    backup = device.dut.backup_power
    # NativeMockPower advertises ITS package's generated client; the device rebinds the node to
    # THIS package's generated PowerClient (a different class over the same wire contract).
    assert type(backup) is PowerClient, type(backup)
    backup.on()
    assert list(backup.read()) == [
        PowerReading(voltage=5.0, current=1.0),
        PowerReading(voltage=5.0, current=2.0),
    ]
    backup.off()
    assert [r.voltage for r in backup.read()] == [0.0, 0.0]


def test_the_tree_mirrors_the_config(device):
    # Both nodes hang off the `dut` composite exactly as configured.
    assert {"power", "backup_power"} <= set(device.dut._node.children.keys())

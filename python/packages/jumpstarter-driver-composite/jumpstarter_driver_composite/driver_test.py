from jumpstarter_driver_power.driver import MockPower
from pydantic.dataclasses import dataclass

from .driver import Composite, Proxy
from jumpstarter.client.base import StubDriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


class MissingClientDriver(Driver):
    """Test driver that returns a non-existent client class path."""

    @classmethod
    def client(cls) -> str:
        return "nonexistent_driver_package.client.NonExistentClient"


# Mock serial driver with a connect() method
@dataclass(kw_only=True)
class MockSerial(Driver):
    connected: bool = False

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    def connect(self):
        self.connected = True
        return "connected"

    @export
    def read(self):
        return "data"


# Mock parent driver that accesses proxy child methods
@dataclass(kw_only=True)
class MockParent(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    def initialize(self):
        # This simulates RideSX accessing self.children["serial"].connect()
        result = self.children["serial"].connect()
        return f"initialized with {result}"


def test_drivers_composite():
    with serve(
        Composite(
            children={
                "proxy0": Proxy(ref="composite1.power1"),
                "proxy1": Proxy(ref="composite1"),
                "power0": MockPower(),
                "composite1": Composite(
                    children={
                        "power1": MockPower(),
                    },
                ),
            },
        )
    ) as client:
        client.power0.on()
        client.composite1.power1.on()
        client.proxy0.on()
        client.proxy1.power1.on()


def test_proxy_method_forwarding():
    """Test that Proxy forwards method calls to target driver"""
    # Server-side test: verify __getattr__ works on Proxy
    actual_serial = MockSerial()
    proxy = Proxy(ref="test")
    composite = Composite(
        children={
            "proxy_serial": proxy,
            "test": actual_serial,
        }
    )

    # Simulate enumerate() being called (happens during serve())
    composite.enumerate()

    # Now test that proxy forwards method calls to target
    result = proxy.connect()
    assert result == "connected"
    assert actual_serial.connected is True

    data = proxy.read()
    assert data == "data"


def test_cli_skips_uninstalled_child_drivers():
    """Composite CLI should skip stub clients for missing driver packages."""
    with serve(
        Composite(
            children={
                "power": MockPower(),
                "missing": MissingClientDriver(),
            },
        )
    ) as client:
        assert isinstance(client.children["missing"], StubDriverClient)

        cli_group = client.cli()
        assert "power" in cli_group.commands
        assert "missing" not in cli_group.commands


def test_cli_all_stubs_produces_empty_group():
    """Composite CLI with only stub children should produce a valid empty group."""
    with serve(
        Composite(
            children={
                "missing1": MissingClientDriver(),
                "missing2": MissingClientDriver(),
            },
        )
    ) as client:
        cli_group = client.cli()
        assert cli_group.commands == {}


def test_cli_all_installed_registers_all_commands():
    """Composite CLI with all installed children should register every command."""
    with serve(
        Composite(
            children={
                "power0": MockPower(),
                "power1": MockPower(),
            },
        )
    ) as client:
        cli_group = client.cli()
        assert "power0" in cli_group.commands
        assert "power1" in cli_group.commands
        assert len(cli_group.commands) == len(client.children)


def test_proxy_in_parent_child():
    """Test that parent driver can call methods on Proxy child (RideSX scenario)"""
    # Server-side test: verify parent accessing self.children["serial"].method()
    actual_serial = MockSerial()
    proxy = Proxy(ref="actual_serial")
    parent = MockParent(
        children={
            "serial": proxy,
        }
    )
    composite = Composite(
        children={
            "parent": parent,
            "actual_serial": actual_serial,
        }
    )

    # Simulate enumerate() being called (happens during serve())
    composite.enumerate()

    # Now test that parent.initialize() works, which internally calls
    # self.children["serial"].connect() on the Proxy
    result = parent.initialize()
    assert result == "initialized with connected"
    assert actual_serial.connected is True

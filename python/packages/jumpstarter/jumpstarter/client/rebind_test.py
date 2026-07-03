"""rebind_client / resolve_root_child — the runtime half of generated device wrappers."""

import pytest
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_power.driver import MockPower

from jumpstarter.client.base import DriverClient, StubDriverClient, rebind_client, resolve_root_child
from jumpstarter.common.utils import serve


class ExtendedPowerClient(PowerClient):
    def voltage_after_on(self) -> None:
        self.on()


def test_rebind_constructs_the_requested_class_over_the_same_node():
    with serve(MockPower()) as client:
        assert isinstance(client, PowerClient)
        rebound = rebind_client(client, ExtendedPowerClient)
        assert isinstance(rebound, ExtendedPowerClient)
        assert rebound.uuid == client.uuid
        rebound.on()  # drives the same served driver
        rebound.off()


def test_rebind_keeps_an_already_satisfying_client():
    with serve(MockPower()) as client:
        assert rebind_client(client, PowerClient) is client
        rebound = rebind_client(client, ExtendedPowerClient)
        # A richer subclass already satisfies the base — preserved as-is.
        assert rebind_client(rebound, PowerClient) is rebound


def test_rebind_upgrades_a_stub_client():
    with serve(MockPower()) as client:
        stub = StubDriverClient(
            uuid=client.uuid,
            labels=client.labels,
            session=client.session,
            portal=client.portal,
            stack=client.stack,
        )
        upgraded = rebind_client(stub, PowerClient)
        assert isinstance(upgraded, PowerClient)
        upgraded.on()
        upgraded.off()


def test_resolve_root_child_handles_all_tree_shapes():
    with serve(MockPower()) as client:
        # Bare harness serve(): the root has no name label and no children — resolve to it.
        assert "jumpstarter.dev/name" not in client.labels
        assert resolve_root_child(client, "power") is client

        # Config-served single entry: the root carries the export key as its name.
        named_root = rebind_client(client, PowerClient)
        named_root.labels = {**client.labels, "jumpstarter.dev/name": "power"}
        assert resolve_root_child(named_root, "power") is named_root

        # Hub shape: the entry is a child of the root composite.
        fake_root = DriverClient(
            uuid=client.uuid,
            labels={},
            session=client.session,
            portal=client.portal,
            stack=client.stack,
            children={"power": client},
        )
        assert resolve_root_child(fake_root, "power") is client

        # A named root does NOT match a different first segment.
        with pytest.raises(KeyError):
            resolve_root_child(named_root, "storage")

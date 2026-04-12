"""Auto-generated typed wrapper for ExporterClass example-board.

Do not edit — regenerate with `jmp codegen` when the ExporterClass changes.
"""

from __future__ import annotations

from jumpstarter_driver_network.client import NetworkClient
from jumpstarter_driver_opendal.client import StorageMuxClient
from jumpstarter_driver_power.client import PowerClient


class ExampleBoardDevice:
    """Typed device wrapper for ExporterClass example-board.

    Composes per-interface clients into a single device object with
    named, typed accessors. Required interfaces are non-nullable;
    optional interfaces may be None.

    Do not edit — regenerate with `jmp codegen`.
    """

    power: PowerClient  # Control power delivery to a device under test.
    storage: StorageMuxClient  # Switch storage media between host and device under test.
    network: NetworkClient | None  # Bidirectional byte stream connection to a network endpoint.

    def __init__(self, client):
        """Initialize ExampleBoardDevice from a connected Jumpstarter client.

        Args:
            client: The root DriverClient returned by env() or a lease.
                Children are accessed by name from client.children.
        """
        self.power = client.children["power"]
        self.storage = client.children["storage"]
        self.network = client.children.get("network")

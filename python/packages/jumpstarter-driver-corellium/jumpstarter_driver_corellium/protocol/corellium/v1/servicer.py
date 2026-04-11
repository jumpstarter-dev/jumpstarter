"""Auto-generated gRPC servicer adapter for Corellium.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

import grpc

from . import corellium_pb2, corellium_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.corellium.v1.Corellium"


def _register():
    """Register the Corellium servicer adapter."""
    from jumpstarter_driver_corellium.driver import Corellium

    register_servicer_adapter(
        interface_class=Corellium,
        service_name=SERVICE_NAME,
        servicer_factory=CorelliumServicer,
        add_to_server=corellium_pb2_grpc.add_CorelliumServicer_to_server,
    )


class CorelliumServicer(corellium_pb2_grpc.CorelliumServicer):
    """gRPC servicer that bridges Corellium to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry


# Register the adapter at import time so the Session can discover it.
_register()

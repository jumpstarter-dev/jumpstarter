"""Auto-generated gRPC servicer adapter for CompositeInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

import grpc

from . import composite_pb2, composite_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.composite.v1.CompositeInterface"


def _register():
    """Register the CompositeInterface servicer adapter."""
    from jumpstarter_driver_composite.driver import CompositeInterface

    register_servicer_adapter(
        interface_class=CompositeInterface,
        service_name=SERVICE_NAME,
        servicer_factory=CompositeInterfaceServicer,
        add_to_server=composite_pb2_grpc.add_CompositeInterfaceServicer_to_server,
    )


class CompositeInterfaceServicer(composite_pb2_grpc.CompositeInterfaceServicer):
    """gRPC servicer that bridges CompositeInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry


# Register the adapter at import time so the Session can discover it.
_register()

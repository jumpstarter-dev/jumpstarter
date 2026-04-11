"""Auto-generated gRPC servicer adapter for CorelliumConsole.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

import grpc

from . import corellium_console_pb2, corellium_console_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.corellium_console.v1.CorelliumConsole"


def _register():
    """Register the CorelliumConsole servicer adapter."""
    from jumpstarter_driver_corellium.driver import CorelliumConsole

    register_servicer_adapter(
        interface_class=CorelliumConsole,
        service_name=SERVICE_NAME,
        servicer_factory=CorelliumConsoleServicer,
        add_to_server=corellium_console_pb2_grpc.add_CorelliumConsoleServicer_to_server,
    )


class CorelliumConsoleServicer(corellium_console_pb2_grpc.CorelliumConsoleServicer):
    """gRPC servicer that bridges CorelliumConsole to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry


# Register the adapter at import time so the Session can discover it.
_register()

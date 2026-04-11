"""Auto-generated gRPC servicer adapter for SSHMITM.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import sshmitm_pb2, sshmitm_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.sshmitm.v1.SSHMITM"


def _register():
    """Register the SSHMITM servicer adapter."""
    from jumpstarter_driver_ssh_mitm.driver import SSHMITM

    register_servicer_adapter(
        interface_class=SSHMITM,
        service_name=SERVICE_NAME,
        servicer_factory=SSHMITMServicer,
        add_to_server=sshmitm_pb2_grpc.add_SSHMITMServicer_to_server,
    )


class SSHMITMServicer(sshmitm_pb2_grpc.SSHMITMServicer):
    """gRPC servicer that bridges SSHMITM to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )


# Register the adapter at import time so the Session can discover it.
_register()

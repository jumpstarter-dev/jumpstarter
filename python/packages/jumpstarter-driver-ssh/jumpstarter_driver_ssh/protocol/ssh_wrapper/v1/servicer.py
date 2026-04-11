"""Auto-generated gRPC servicer adapter for SSHWrapper.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import ssh_wrapper_pb2, ssh_wrapper_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.ssh_wrapper.v1.SSHWrapper"


def _register():
    """Register the SSHWrapper servicer adapter."""
    from jumpstarter_driver_ssh.driver import SSHWrapper

    register_servicer_adapter(
        interface_class=SSHWrapper,
        service_name=SERVICE_NAME,
        servicer_factory=SSHWrapperServicer,
        add_to_server=ssh_wrapper_pb2_grpc.add_SSHWrapperServicer_to_server,
    )


class SSHWrapperServicer(ssh_wrapper_pb2_grpc.SSHWrapperServicer):
    """gRPC servicer that bridges SSHWrapper to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetDefaultUsername(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_default_username):
            result = await driver.get_default_username()
        else:
            result = driver.get_default_username()
        return ssh_wrapper_pb2.GetDefaultUsernameResponse(value=result)

    async def GetSshCommand(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_ssh_command):
            result = await driver.get_ssh_command()
        else:
            result = driver.get_ssh_command()
        return ssh_wrapper_pb2.GetSshCommandResponse(value=result)

    async def GetSshIdentity(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_ssh_identity):
            result = await driver.get_ssh_identity()
        else:
            result = driver.get_ssh_identity()
        return ssh_wrapper_pb2.GetSshIdentityResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()

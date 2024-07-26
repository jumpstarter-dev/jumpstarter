"""
Base classes for drivers and driver clients
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from anyio.from_thread import BlockingPortal
from anyio.streams.file import FileReadStream
from grpc import StatusCode

from jumpstarter.common import Interface, Metadata
from jumpstarter.common.streams import (
    create_memory_stream,
    forward_server_stream,
)
from jumpstarter.drivers.core import AsyncDriverClient
from jumpstarter.drivers.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc


@dataclass(kw_only=True)
class Driver(
    Metadata,
    Interface,
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
):
    """Base class for drivers

    Drivers should as the minimum implement the
    `interface` and `version` methods.

    Additional driver calls can be implemented as regular
    sync/async regular/generator methods and marked with
    the `export` decorator.
    """

    resources: dict[UUID, Any] = field(default_factory=dict, init=False)
    """Dict of client side resources"""

    def add_to_server(self, server):
        """Add self to grpc server

        Useful for unit testing.
        """
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    async def DriverCall(self, request, context):
        method = await self.__lookup_drivercall(request.method, context, MARKER_DRIVERCALL)

        return await method(request, context)

    async def StreamingDriverCall(self, request, context):
        method = await self.__lookup_drivercall(request.method, context, MARKER_STREAMING_DRIVERCALL)

        async for v in method(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        metadata = dict(context.invocation_metadata())

        match metadata["kind"]:
            case "connect":
                method = await self.__lookup_drivercall("connect", context, MARKER_STREAMCALL)

                async for v in method(request_iterator, context):
                    yield v

            case "resource":
                remote, resource = create_memory_stream()

                resource_uuid = uuid4()

                self.resources[resource_uuid] = resource

                await resource.send(str(resource_uuid).encode("utf-8"))

                async with remote:
                    async for v in forward_server_stream(request_iterator, remote):
                        yield v

                del self.resources[resource_uuid]

    async def GetReport(self, request, context):
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=[
                jumpstarter_pb2.DriverInstanceReport(
                    uuid=str(uuid),
                    parent_uuid=str(parent_uuid) if parent_uuid else None,
                    labels=instance.labels
                    | {
                        "jumpstarter.dev/client_module": instance.client_module(),
                        "jumpstarter.dev/client_class": instance.client_class(),
                    },
                )
                for (uuid, parent_uuid, instance) in self.items()
            ],
        )

    def items(self, parent=None):
        """Get list of self and child devices"""

        return [(self.uuid, parent.uuid if parent else None, self)]

    async def __lookup_drivercall(self, name, context, marker):
        """Lookup drivercall by method name

        Methods are checked against magic markers
        to avoid accidentally calling non-exported
        methods
        """
        method = getattr(self, name, None)

        if method is None:
            await context.abort(StatusCode.NOT_FOUND, f"method {name} not found on driver")

        if getattr(method, marker, None) != MARKER_MAGIC:
            await context.abort(StatusCode.NOT_FOUND, f"method {name} missing marker {marker}")

        return method


@dataclass(kw_only=True)
class DriverClient(AsyncDriverClient):
    """Base class for driver clients

    Driver clients should as the minimum implement the
    `client_module` and `client_class` methods, which
    points to the corresponding driver client.

    Additional client methods can be implemented as
    regular methods and call `call` or
    `streamingcall` internally.
    """

    portal: BlockingPortal

    def call(self, method, *args):
        """Make DriverCall by method name and arguments"""
        return self.portal.call(self.async_call, method, *args)

    def streamingcall(self, method, *args):
        generator = self.portal.call(self.async_streamingcall, method, *args)
        while True:
            try:
                yield self.portal.call(generator.__anext__)
            except StopAsyncIteration:
                break

    @contextmanager
    def local_file(
        self,
        filepath,
    ):
        with self.portal.wrap_async_context_manager(self.portal.call(FileReadStream.from_path, filepath)) as file:
            with self.portal.wrap_async_context_manager(self.resource(file)) as uuid:
                yield uuid

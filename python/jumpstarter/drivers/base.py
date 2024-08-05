"""
Base classes for drivers and driver clients
"""

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from anyio.from_thread import BlockingPortal
from grpc import StatusCode

from jumpstarter.common import Metadata
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
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
    metaclass=ABCMeta,
):
    """Base class for drivers

    Drivers should at the minimum implement the `client` method.

    Regular or streaming driver calls can be marked with the `export` decorator.
    Raw stream constructors can be marked with the `exportstream` decorator.
    """

    resources: dict[UUID, Any] = field(default_factory=dict, init=False)
    """Dict of client side resources"""

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """
        Return full import path of the corresponding driver client class
        """

    def add_to_server(self, server):
        """Add self to grpc server

        Useful for unit testing.

        :meta private:
        """
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    async def DriverCall(self, request, context):
        """
        :meta private:
        """
        method = await self.__lookup_drivercall(request.method, context, MARKER_DRIVERCALL)

        return await method(request, context)

    async def StreamingDriverCall(self, request, context):
        """
        :meta private:
        """
        method = await self.__lookup_drivercall(request.method, context, MARKER_STREAMING_DRIVERCALL)

        async for v in method(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        """
        :meta private:
        """
        metadata = dict(context.invocation_metadata())

        match metadata["kind"]:
            case "connect":
                method = await self.__lookup_drivercall(metadata["method"], context, MARKER_STREAMCALL)

                async for v in method(request_iterator, context):
                    yield v

            case "resource":
                remote, resource = create_memory_stream()

                resource_uuid = uuid4()

                self.resources[resource_uuid] = resource

                await resource.send(str(resource_uuid).encode("utf-8"))
                await resource.send_eof()

                async with remote:
                    async for v in forward_server_stream(request_iterator, remote):
                        yield v

                # del self.resources[resource_uuid]
                # small resources might be fully buffered in memory

    async def GetReport(self, request, context):
        """
        :meta private:
        """
        return jumpstarter_pb2.GetReportResponse(
            uuid=str(self.uuid),
            labels=self.labels,
            reports=[
                jumpstarter_pb2.DriverInstanceReport(
                    uuid=str(uuid),
                    parent_uuid=str(parent_uuid) if parent_uuid else None,
                    labels=instance.labels | {"jumpstarter.dev/client": instance.client()},
                )
                for (uuid, parent_uuid, instance) in self.items()
            ],
        )

    def items(self, parent=None):
        """
        Get list of self and child devices

        :meta private:
        """

        return [(self.uuid, parent.uuid if parent else None, self)]

    def resource(self, uuid: str):
        return self.resources[UUID(uuid)]

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

    Client methods can be implemented as regular functions,
    and call the `call` or `streamingcall` helpers internally
    to invoke exported methods on the driver.

    Additional client functionalities such as raw stream
    connections or sharing client-side resources can be added
    by inheriting mixin classes under `jumpstarter.drivers.mixins`
    """

    portal: BlockingPortal

    def call(self, method, *args):
        """
        Invoke driver call

        :param str method: method name of driver call
        :param list[Any] args: arguments for driver call

        :return: driver call result
        :rtype: Any
        """
        return self.portal.call(self.call_async, method, *args)

    def streamingcall(self, method, *args):
        """
        Invoke streaming driver call

        :param str method: method name of streaming driver call
        :param list[Any] args: arguments for streaming driver call

        :return: streaming driver call result
        :rtype: Generator[Any, None, None]
        """
        generator = self.portal.call(self.streamingcall_async, method, *args)
        while True:
            try:
                yield self.portal.call(generator.__anext__)
            except StopAsyncIteration:
                break

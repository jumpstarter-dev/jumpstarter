# This file contains the base class for all jumpstarter drivers
from google.protobuf import struct_pb2, json_format
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc
from dataclasses import dataclass, asdict, is_dataclass
from uuid import UUID, uuid4
from typing import Any, BinaryIO, Final
from dataclasses import field
from jumpstarter.common.streams import (
    create_memory_stream,
    forward_client_stream,
    forward_server_stream,
)
from jumpstarter.common import Metadata
from contextvars import ContextVar
from contextlib import asynccontextmanager
from abc import ABC, abstractmethod
from grpc import StatusCode
import anyio
import grpc


ContextStore = ContextVar("store")

MARKER_MAGIC: Final[str] = "07c9b9cc"
MARKER_DRIVERCALL: Final[str] = "marker_drivercall"
MARKER_STREAMING_DRIVERCALL: Final[str] = "marker_streamingdrivercall"


@dataclass(kw_only=True)
class Store:
    fds: list[BinaryIO] = field(default_factory=list, init=False)
    conns: dict[UUID, Any] = field(default_factory=dict, init=False)


@dataclass(kw_only=True)
class Driver(
    ABC,
    Metadata,
    jumpstarter_pb2_grpc.ExporterServiceServicer,
    router_pb2_grpc.RouterServiceServicer,
):
    """Base class for drivers

    Drivers should as the minimum implement the
    `interface` and `version` methods.

    Additional driver calls can be implemented as regular
    methods and marked with either the `drivercall`
    decorator for unary calls or the `streamingdrivercall`
    decorator for streaming (generator) calls.
    """

    @classmethod
    @abstractmethod
    def interface(cls) -> str:
        """Return interface name of the driver.

        Names should be globally unique thus should
        be namespaced like `example.com/foo`
        """

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        """Return interface version of the driver.

        Versions are matched exactly and don't have
        to follow semantic versioning.
        """

    async def DriverCall(self, request, context):
        method = await self.__lookup_drivercall(request, context, MARKER_DRIVERCALL)

        return await method(request, context)

    async def StreamingDriverCall(self, request, context):
        method = await self.__lookup_drivercall(request, context, MARKER_STREAMING_DRIVERCALL)

        async for v in method(request, context):
            yield v

    async def Stream(self, request_iterator, context):
        async with self.connect() as stream:
            async for v in forward_server_stream(request_iterator, stream):
                yield v

    def Reports(self, parent=None) -> list[jumpstarter_pb2.DriverInstanceReport]:
        return [
            jumpstarter_pb2.DriverInstanceReport(
                uuid=str(self.uuid),
                parent_uuid=str(parent.uuid) if parent else None,
                labels=self.labels
                | {
                    "jumpstarter.dev/interface": self.interface(),
                    "jumpstarter.dev/version": self.version(),
                },
            )
        ]

    def items(self):
        return [(self.uuid, self)]

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)

    async def __lookup_drivercall(self, request, context, marker):
        """Lookup drivercall by method name

        Methods are checked against magic markers
        to avoid accidentally calling non-exported
        methods
        """
        method = getattr(self, request.method, None)

        if method is None:
            await context.abort(StatusCode.NOT_FOUND, f"method {request.method} not found on driver")

        if getattr(method, marker, None) != MARKER_MAGIC:
            await context.abort(StatusCode.NOT_FOUND, f"method {request.method} missing marker {marker}")

        return method


@dataclass(kw_only=True)
class DriverClient(Metadata):
    channel: grpc.aio.Channel
    stub: jumpstarter_pb2_grpc.ExporterServiceStub = field(init=False)
    router: router_pb2_grpc.RouterServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(self.channel)
        self.router = router_pb2_grpc.RouterServiceStub(self.channel)

    async def _drivercall(self, method, *args):
        return json_format.MessageToDict(
            (
                await self.stub.DriverCall(
                    jumpstarter_pb2.DriverCallRequest(
                        uuid=str(self.uuid),
                        method=method,
                        args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
                    )
                )
            ).result
        )

    async def _streamingdrivercall(self, method, *args):
        async for v in self.stub.StreamingDriverCall(
            jumpstarter_pb2.StreamingDriverCallRequest(
                uuid=str(self.uuid),
                method=method,
                args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
            )
        ):
            yield json_format.MessageToDict(v.result)

    @asynccontextmanager
    async def _stream(self):
        client_stream, device_stream = create_memory_stream()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                forward_client_stream,
                self.router,
                device_stream,
                {"kind": "device", "uuid": str(self.uuid)}.items(),
            )
            async with client_stream:
                yield client_stream

    @asynccontextmanager
    async def _portforward(self, listener):
        async def handle(client):
            async with client:
                await forward_client_stream(
                    self.router,
                    client,
                    {"kind": "device", "uuid": str(self.uuid)}.items(),
                )

        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()


def drivercall(func):
    async def DriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        result = await func(self, *args)

        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=json_format.ParseDict(asdict(result) if is_dataclass(result) else result, struct_pb2.Value()),
        )

    setattr(DriverCall, MARKER_DRIVERCALL, MARKER_MAGIC)

    return DriverCall


def streamingdrivercall(func):
    async def StreamingDriverCall(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        async for result in func(self, *args):
            yield jumpstarter_pb2.StreamingDriverCallResponse(
                uuid=str(uuid4()),
                result=json_format.ParseDict(
                    asdict(result) if is_dataclass(result) else result,
                    struct_pb2.Value(),
                ),
            )

    setattr(StreamingDriverCall, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)

    return StreamingDriverCall

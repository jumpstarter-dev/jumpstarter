# This file contains the base class for all jumpstarter drivers
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, BinaryIO
from uuid import UUID

import anyio
import grpc
from google.protobuf import json_format, struct_pb2
from grpc import StatusCode

from jumpstarter.common import Metadata
from jumpstarter.common.streams import (
    create_memory_stream,
    forward_client_stream,
)
from jumpstarter.drivers.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc

ContextStore = ContextVar("store")


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
        """Return interface name of the driver

        Names should be globally unique thus should
        be namespaced like `example.com/foo`.
        """

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        """Return interface version of the driver

        Versions are matched exactly and don't have
        to follow semantic versioning.
        """

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
        method = await self.__lookup_drivercall("connect", context, MARKER_STREAMCALL)

        async for v in method(request_iterator, context):
            yield v

    def Reports(self) -> list[jumpstarter_pb2.DriverInstanceReport]:
        return [
            jumpstarter_pb2.DriverInstanceReport(
                uuid=str(uuid),
                parent_uuid=str(parent_uuid) if parent_uuid else None,
                labels=instance.labels
                | {
                    "jumpstarter.dev/interface": instance.interface(),
                    "jumpstarter.dev/version": instance.version(),
                },
            )
            for (uuid, parent_uuid, instance) in self.items()
        ]

    def items(self, parent=None):
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

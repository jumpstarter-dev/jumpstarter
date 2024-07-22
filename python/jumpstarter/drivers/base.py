# This file contains the base class for all jumpstarter drivers
from google.protobuf import struct_pb2, json_format
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc
from dataclasses import dataclass, asdict, is_dataclass
from uuid import UUID, uuid4
from typing import Any, BinaryIO
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
import anyio
import grpc


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
    @classmethod
    @abstractmethod
    def interface(cls) -> str: ...

    @classmethod
    @abstractmethod
    def version(cls) -> str: ...

    def items(self):
        return [(self.uuid, self)]

    async def DriverCall(self, request, context):
        method = getattr(self, request.method)

        if not getattr(method, "is_drivercall", False):
            raise ValueError("no matching driver call")

        return await method(request, context)

    async def StreamingDriverCall(self, request, context):
        method = getattr(self, request.method)

        if not getattr(method, "is_streamingdrivercall", False):
            raise ValueError("no matching streaming driver call")

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

    def add_to_server(self, server):
        jumpstarter_pb2_grpc.add_ExporterServiceServicer_to_server(self, server)
        router_pb2_grpc.add_RouterServiceServicer_to_server(self, server)


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
                        args=[
                            json_format.ParseDict(arg, struct_pb2.Value())
                            for arg in args
                        ],
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
            result=json_format.ParseDict(
                asdict(result) if is_dataclass(result) else result, struct_pb2.Value()
            ),
        )

    DriverCall.is_drivercall = True

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

    StreamingDriverCall.is_streamingdrivercall = True

    return StreamingDriverCall

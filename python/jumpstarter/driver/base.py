"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import field
from inspect import isasyncgenfunction, iscoroutinefunction
from itertools import chain
from typing import Any
from uuid import UUID, uuid4

import aiohttp
from anyio import to_thread
from google.protobuf import json_format, struct_pb2
from grpc import StatusCode
from pydantic import BaseModel, TypeAdapter
from pydantic.dataclasses import dataclass

from jumpstarter.common import Metadata
from jumpstarter.common.resources import ClientStreamResource, PresignedRequestResource, Resource, ResourceMetadata
from jumpstarter.common.streams import (
    DriverStreamRequest,
    ResourceStreamRequest,
)
from jumpstarter.streams import AiohttpStreamReaderStream, MetadataStream, create_memory_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc

from .decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)


def encode_value(v):
    return json_format.ParseDict(
        v.model_dump(mode="json") if isinstance(v, BaseModel) else v,
        struct_pb2.Value(),
    )


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

    children: dict[str, Driver] = field(default_factory=dict)

    resources: dict[UUID, Any] = field(default_factory=dict, init=False)
    """Dict of client side resources"""

    def __post_init__(self):
        super().__post_init__()

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """
        Return full import path of the corresponding driver client class
        """

    async def DriverCall(self, request, context):
        """
        :meta private:
        """
        method = await self.__lookup_drivercall(request.method, context, MARKER_DRIVERCALL)

        args = [json_format.MessageToDict(arg) for arg in request.args]

        if iscoroutinefunction(method):
            result = await method(*args)
        else:
            result = await to_thread.run_sync(method, *args)

        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=encode_value(result),
        )

    async def StreamingDriverCall(self, request, context):
        """
        :meta private:
        """
        method = await self.__lookup_drivercall(request.method, context, MARKER_STREAMING_DRIVERCALL)

        args = [json_format.MessageToDict(arg) for arg in request.args]

        if isasyncgenfunction(method):
            async for result in method(*args):
                yield jumpstarter_pb2.StreamingDriverCallResponse(
                    uuid=str(uuid4()),
                    result=encode_value(result),
                )
        else:
            for result in await to_thread.run_sync(method, *args):
                yield jumpstarter_pb2.StreamingDriverCallResponse(
                    uuid=str(uuid4()),
                    result=encode_value(result),
                )

    @asynccontextmanager
    async def Stream(self, request, context):
        """
        :meta private:
        """
        match request:
            case DriverStreamRequest(method=driver_method):
                method = await self.__lookup_drivercall(driver_method, context, MARKER_STREAMCALL)

                async with method() as stream:
                    yield stream

            case ResourceStreamRequest():
                remote, resource = create_memory_stream()

                resource_uuid = uuid4()

                self.resources[resource_uuid] = resource

                async with MetadataStream(
                    stream=remote,
                    metadata=ResourceMetadata.model_construct(
                        resource=ClientStreamResource(uuid=resource_uuid)
                    ).model_dump(mode="json", round_trip=True),
                ) as stream:
                    yield stream

    def report(self, *, parent=None, name=None):
        """
        Create DriverInstanceReport

        :meta private:
        """
        return jumpstarter_pb2.DriverInstanceReport(
            uuid=str(self.uuid),
            parent_uuid=str(parent.uuid) if parent else None,
            labels=self.labels
            | ({"jumpstarter.dev/client": self.client()})
            | ({"jumpstarter.dev/name": name} if name else {}),
        )

    def enumerate(self, *, parent=None, name=None):
        """
        Get list of self and child devices

        :meta private:
        """

        return [(self.uuid, parent, name, self)] + list(
            chain(*[child.enumerate(parent=self, name=cname) for (cname, child) in self.children.items()])
        )

    @asynccontextmanager
    async def resource(self, handle: str):
        handle = TypeAdapter(Resource).validate_python(handle)
        match handle:
            case ClientStreamResource(uuid=uuid):
                async with self.resources[uuid] as stream:
                    yield stream
                del self.resources[uuid]
            case PresignedRequestResource(headers=headers, url=url, method=method):
                async with aiohttp.request(method, url, headers=headers, raise_for_status=True) as resp:
                    async with AiohttpStreamReaderStream(reader=resp.content) as stream:
                        yield stream

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

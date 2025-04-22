"""
Base classes for drivers and driver clients
"""

from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import field
from inspect import isasyncgenfunction, iscoroutinefunction
from itertools import chain
from typing import Any
from uuid import UUID, uuid4

import aiohttp
from anyio import to_thread
from grpc import StatusCode
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass

from .decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMCALL,
    MARKER_STREAMING_DRIVERCALL,
)
from jumpstarter.common import Metadata
from jumpstarter.common.resources import ClientStreamResource, PresignedRequestResource, Resource, ResourceMetadata
from jumpstarter.common.serde import decode_value, encode_value
from jumpstarter.common.streams import (
    DriverStreamRequest,
    ResourceStreamRequest,
)
from jumpstarter.streams.aiohttp import AiohttpStreamReaderStream
from jumpstarter.streams.common import create_memory_stream
from jumpstarter.streams.metadata import MetadataStream
from jumpstarter.streams.progress import ProgressStream


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

    log_level: str = "INFO"
    logger: logging.Logger = field(init=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(self.log_level)

    def close(self):
        for child in self.children.values():
            child.close()

    def reset(self):
        for child in self.children.values():
            child.reset()

    @classmethod
    @abstractmethod
    def client(cls) -> str:
        """
        Return full import path of the corresponding driver client class
        """

    def extra_labels(self) -> dict[str, str]:
        return {}

    async def DriverCall(self, request, context):
        """
        :meta private:
        """
        try:
            method = await self.__lookup_drivercall(request.method, context, MARKER_DRIVERCALL)

            args = [decode_value(arg) for arg in request.args]

            if iscoroutinefunction(method):
                result = await method(*args)
            else:
                result = await to_thread.run_sync(method, *args)

            return jumpstarter_pb2.DriverCallResponse(
                uuid=str(uuid4()),
                result=encode_value(result),
            )
        except NotImplementedError as e:
            await context.abort(StatusCode.UNIMPLEMENTED, str(e))
        except ValueError as e:
            await context.abort(StatusCode.INVALID_ARGUMENT, str(e))
        except TimeoutError as e:
            await context.abort(StatusCode.DEADLINE_EXCEEDED, str(e))
        except Exception as e:
            await context.abort(StatusCode.UNKNOWN, str(e))

    async def StreamingDriverCall(self, request, context):
        """
        :meta private:
        """
        try:
            method = await self.__lookup_drivercall(request.method, context, MARKER_STREAMING_DRIVERCALL)

            args = [decode_value(arg) for arg in request.args]

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
        except NotImplementedError as e:
            await context.abort(StatusCode.UNIMPLEMENTED, str(e))
        except ValueError as e:
            await context.abort(StatusCode.INVALID_ARGUMENT, str(e))
        except TimeoutError as e:
            await context.abort(StatusCode.DEADLINE_EXCEEDED, str(e))
        except Exception as e:
            await context.abort(StatusCode.UNKNOWN, str(e))

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

    def report(self, *, root=None, parent=None, name=None):
        """
        Create DriverInstanceReport

        :meta private:
        """

        if root is None:
            root = self

        return jumpstarter_pb2.DriverInstanceReport(
            uuid=str(self.uuid),
            parent_uuid=str(parent.uuid) if parent else None,
            labels=self.labels
            | self.extra_labels()
            | ({"jumpstarter.dev/client": self.client()})
            | ({"jumpstarter.dev/name": name} if name else {}),
        )

    def enumerate(self, *, root=None, parent=None, name=None):
        """
        Get list of self and child devices

        :meta private:
        """
        if root is None:
            root = self

        return [(self.uuid, parent, name, self)] + list(
            chain(*[child.enumerate(root=root, parent=self, name=cname) for (cname, child) in self.children.items()])
        )

    @asynccontextmanager
    async def resource(self, handle: str, timeout: int = 300):
        handle = TypeAdapter(Resource).validate_python(handle)
        match handle:
            case ClientStreamResource(uuid=uuid):
                async with self.resources[uuid] as stream:
                    try:
                        yield stream
                    finally:
                        del self.resources[uuid]
            case PresignedRequestResource(headers=headers, url=url, method=method):
                client_timeout = aiohttp.ClientTimeout(total=timeout)
                match method:
                    case "GET":
                        async with aiohttp.request(
                            method, url, headers=headers, raise_for_status=True, timeout=client_timeout
                        ) as resp:
                            async with AiohttpStreamReaderStream(reader=resp.content) as stream:
                                yield ProgressStream(stream=stream, logging=True)
                    case "PUT":
                        remote, stream = create_memory_stream()
                        async with aiohttp.request(
                            method, url, headers=headers, raise_for_status=True, data=remote, timeout=client_timeout
                        ) as resp:
                            async with stream:
                                yield ProgressStream(stream=stream, logging=True)
                    case _:
                        # INVARIANT: method is always one of GET or PUT, see PresignedRequestResource
                        raise ValueError("unreachable")

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

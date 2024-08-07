from inspect import isasyncgenfunction, iscoroutinefunction, isfunction, isgeneratorfunction
from typing import Final
from uuid import uuid4

from anyio import to_thread
from google.protobuf import json_format, struct_pb2
from pydantic import BaseModel

from jumpstarter.common.streams import (
    forward_server_stream,
)
from jumpstarter.v1 import jumpstarter_pb2

MARKER_MAGIC: Final[str] = "07c9b9cc"
MARKER_DRIVERCALL: Final[str] = "marker_drivercall"
MARKER_STREAMCALL: Final[str] = "marker_streamcall"
MARKER_STREAMING_DRIVERCALL: Final[str] = "marker_streamingdrivercall"


def export(func):
    """
    Decorator for exporting method as driver call
    """
    if isasyncgenfunction(func) or isgeneratorfunction(func):
        return streamingdrivercall(func)
    elif iscoroutinefunction(func) or isfunction(func):
        return drivercall(func)
    else:
        raise ValueError(f"unsupported exported function {func}")


def drivercall(func):
    async def wrapper(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        if iscoroutinefunction(func):
            result = await func(self, *args)
        else:
            result = await to_thread.run_sync(func, self, *args)

        return jumpstarter_pb2.DriverCallResponse(
            uuid=str(uuid4()),
            result=json_format.ParseDict(
                result.model_dump(mode="json") if isinstance(result, BaseModel) else result, struct_pb2.Value()
            ),
        )

    setattr(wrapper, MARKER_DRIVERCALL, MARKER_MAGIC)

    return wrapper


def exportstream(func):
    """
    Decorator for exporting method as stream
    """

    async def wrapper(self, request_iterator, context):
        async with func(self) as stream:
            async for v in forward_server_stream(request_iterator, stream):
                yield v

    setattr(wrapper, MARKER_STREAMCALL, MARKER_MAGIC)

    return wrapper


def streamingdrivercall(func):
    async def wrapper(self, request, context):
        args = [json_format.MessageToDict(arg) for arg in request.args]

        if isasyncgenfunction(func):
            async for result in func(self, *args):
                yield jumpstarter_pb2.StreamingDriverCallResponse(
                    uuid=str(uuid4()),
                    result=json_format.ParseDict(
                        result.model_dump(mode="json") if isinstance(result, BaseModel) else result,
                        struct_pb2.Value(),
                    ),
                )
        else:
            for result in await to_thread.run_sync(func, self, *args):
                yield jumpstarter_pb2.StreamingDriverCallResponse(
                    uuid=str(uuid4()),
                    result=json_format.ParseDict(
                        result.model_dump(mode="json") if isinstance(result, BaseModel) else result,
                        struct_pb2.Value(),
                    ),
                )

    setattr(wrapper, MARKER_STREAMING_DRIVERCALL, MARKER_MAGIC)

    return wrapper

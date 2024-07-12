# This file contains the base class for all jumpstarter driver stubs
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from jumpstarter.common import Metadata
from google.protobuf import struct_pb2, json_format
from dataclasses import dataclass
from uuid import UUID
from typing import List, Any
import inspect
import anyio


async def driver_call(
    stub: jumpstarter_pb2_grpc.ExporterServiceStub,
    device_uuid: UUID,
    driver_method: str,
    args: List[Any],
):
    request = jumpstarter_pb2.DriverCallRequest(
        device_uuid=str(device_uuid),
        driver_method=driver_method,
        args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
    )

    response = await stub.DriverCall(request)

    return json_format.MessageToDict(response.result)


async def streaming_driver_call(
    stub: jumpstarter_pb2_grpc.ExporterServiceStub,
    device_uuid: UUID,
    driver_method: str,
    args: List[Any],
):
    request = jumpstarter_pb2.StreamingDriverCallRequest(
        device_uuid=str(device_uuid),
        driver_method=driver_method,
        args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
    )

    async for response in stub.StreamingDriverCall(request):
        yield json_format.MessageToDict(response.result)


def build_stub_method(cls, driver_method):
    async def stub_method(self, *args, **kwargs):
        return await driver_call(self.stub, self.uuid, driver_method, args)

    stub_method.__signature = inspect.signature(
        inspect.getattr_static(cls, driver_method)
    )

    return stub_method


def build_streaming_stub_method(cls, driver_method):
    async def streaming_stub_method(self, *args, **kwargs):
        async for v in streaming_driver_call(self.stub, self.uuid, driver_method, args):
            yield v

    streaming_stub_method.__signature = inspect.signature(
        inspect.getattr_static(cls, driver_method)
    )

    return streaming_stub_method


def build_stub_property(name):
    def getter(self):
        async def inner():
            return await driver_call(self.stub, self.uuid, "__get__" + name, [])

        return anyio.from_thread.run(inner)

    def setter(self, value):
        async def inner():
            return await driver_call(self.stub, self.uuid, "__set__" + name, [value])

        return anyio.from_thread.run(inner)

    return property(getter, setter)


@dataclass(kw_only=True)
class DriverStub(Metadata):
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init_subclass__(cls, base, **kwargs):
        for name in inspect.getattr_static(base, "__abstractmethods__"):
            attr = inspect.getattr_static(base, name)
            if callable(attr):
                if inspect.isasyncgenfunction(attr):
                    setattr(
                        cls,
                        name,
                        build_streaming_stub_method(base, name),
                    )
                else:
                    setattr(
                        cls,
                        name,
                        build_stub_method(base, name),
                    )
            elif isinstance(attr, property):
                setattr(
                    cls,
                    name,
                    build_stub_property(name),
                )
            else:
                raise NotImplementedError("unrecognized abstract method")

        super().__init_subclass__(**kwargs)

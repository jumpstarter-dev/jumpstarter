# This file contains the base class for all jumpstarter driver stubs
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import struct_pb2, json_format
from dataclasses import dataclass
from uuid import UUID
from typing import List, Any
from . import DeviceMeta
import inspect


def driver_call(
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

    response = stub.DriverCall(request)

    return json_format.MessageToDict(response.result)


def build_stub_method(cls, driver_method):
    def stub_method(self, *args, **kwargs):
        return driver_call(self.stub, self.uuid, driver_method, args)

    stub_method.__signature = inspect.signature(cls.callables[driver_method])

    return stub_method


def build_stub_property(cls, name):
    getter = build_stub_method(cls, "__get__" + name)
    setter = build_stub_method(cls, "__set__" + name)
    return property(getter, setter)


@dataclass(kw_only=True)
class DriverStub(DeviceMeta):
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init_subclass__(cls, base, **kwargs):
        super().__init_subclass__(**kwargs)

        class subclass(base):
            pass

        for name in subclass.__abstractmethods__:
            attr = inspect.getattr_static(subclass, name)
            if callable(attr):
                setattr(
                    cls,
                    name,
                    build_stub_method(subclass, name),
                )
            elif isinstance(attr, property):
                setattr(
                    cls,
                    name,
                    build_stub_property(subclass, name),
                )

# This file contains the base class for all jumpstarter driver stubs
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2, struct_pb2, json_format
import inspect


def build_stub_method(cls, driver_method):
    def stub_method(self, *args, **kwargs):
        return json_format.MessageToDict(
            self.stub.DriverCall(
                jumpstarter_pb2.DriverCallRequest(
                    device_uuid=self.device_uuid,
                    driver_method=driver_method,
                    args=[
                        json_format.ParseDict(arg, struct_pb2.Value()) for arg in args
                    ],
                )
            ).result
        )

    stub_method.__signature = inspect.signature(cls.callables[driver_method])

    return stub_method


def build_stub_property(cls, name):
    getter = build_stub_method(cls, "get_" + name)
    setter = build_stub_method(cls, "set_" + name)
    return property(getter, setter)


class DriverStub:
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

    def __init__(self, stub, device_uuid):
        super().__init__()

        self.stub = stub
        self.device_uuid = device_uuid

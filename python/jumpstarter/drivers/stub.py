# This file contains the base class for all jumpstarter driver stubs
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc
from google.protobuf import empty_pb2, struct_pb2, json_format
import inspect


# base class for all driver stubs
class DriverStub:
    def __init__(self, stub, cls, device_uuid):
        for driver_method in cls.__abstractmethods__:

            def build_stub_method(driver_method):
                def stub_method(*args, **kwargs):
                    return json_format.MessageToDict(
                        stub.DriverCall(
                            jumpstarter_pb2.DriverCallRequest(
                                device_uuid=device_uuid,
                                driver_method=driver_method,
                                args=[
                                    json_format.ParseDict(arg, struct_pb2.Value())
                                    for arg in args
                                ],
                            )
                        ).result
                    )

                stub_method.__signature = inspect.signature(getattr(cls, driver_method))

                return stub_method

            setattr(self, driver_method, build_stub_method(driver_method))

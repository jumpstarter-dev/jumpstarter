# This file contains the base class for all jumpstarter drivers
from abc import ABC
from typing import List, Any
from collections.abc import Generator
from jumpstarter.v1 import jumpstarter_pb2
from jumpstarter.common import Metadata
from .registry import _registry
import inspect


# base class for all drivers
class DriverBase(ABC, Metadata):
    def __init_subclass__(cls, interface=None, **kwargs):
        if interface:
            cls.interface = interface
            _registry.register(cls)

        cls.callables = dict()
        cls.generator = dict()

        for name in inspect.getattr_static(cls, "__abstractmethods__"):
            attr = inspect.getattr_static(cls, name)
            if callable(attr):
                if inspect.isgeneratorfunction(attr):
                    cls.generator[name] = attr
                else:
                    cls.callables[name] = attr
            elif isinstance(attr, property):
                cls.callables["__get__" + name] = attr.__get__
                cls.callables["__set__" + name] = attr.__set__
            else:
                raise NotImplementedError("unrecognized abstract method")

        super().__init_subclass__(**kwargs)

    def call(self, method: str, args: List[Any]) -> Any:
        function = self.callables.get(method)

        if not function:
            raise NotImplementedError("no such drivercall")

        return function(self, *args)

    def streaming_call(
        self, method: str, args: List[Any]
    ) -> Generator[Any, None, None]:
        function = self.generator.get(method)

        if not function:
            raise NotImplementedError("no such streaming drivercall")

        yield from function(self, *args)

    def reports(self) -> List[jumpstarter_pb2.DeviceReport]:
        return [
            jumpstarter_pb2.DeviceReport(
                device_uuid=str(self.uuid),
                driver_interface=self.interface,
                labels=self.labels,
            )
        ]

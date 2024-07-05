# This file contains the base class for all jumpstarter drivers
from abc import ABC
from typing import List, Any
from jumpstarter.v1 import jumpstarter_pb2
from . import DeviceMeta
from .registry import _registry
import inspect


# base class for all drivers
class DriverBase(ABC, DeviceMeta):
    def __init_subclass__(cls, interface=None, **kwargs):
        if interface:
            cls.interface = interface
            _registry.register(cls)

        cls.callables = dict()

        for name in inspect.getattr_static(cls, "__abstractmethods__"):
            attr = inspect.getattr_static(cls, name)
            if callable(attr):
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

    def report(self) -> jumpstarter_pb2.DeviceReport:
        return jumpstarter_pb2.DeviceReport(
            device_uuid=str(self.uuid),
            driver_interface=self.interface,
            labels=self.labels,
        )

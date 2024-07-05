# This file contains the base class for all jumpstarter drivers
from abc import ABC, abstractmethod
from typing import List, Any
from . import DeviceMeta
import inspect


# base class for all drivers
class DriverBase(ABC, DeviceMeta):
    def __init_subclass__(cls, **kwargs):
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

    @property
    @abstractmethod
    def interface(self): ...

    def call(self, method: str, args: List[Any]) -> Any:
        function = self.callables.get(method)

        if not function:
            raise NotImplementedError("no such drivercall")

        return function(self, *args)

# This file contains the base class for all jumpstarter drivers
from abc import ABC, abstractmethod
from typing import List, Any
from dataclasses import dataclass
from uuid import UUID, uuid4
import inspect


def build_getter(prop):
    return lambda self: prop.fget(self)


def build_setter(prop):
    return lambda self, x: prop.fset(self, x)


# base class for all drivers
@dataclass
class DriverBase(ABC):
    uuid: UUID
    labels: dict[str, str]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        cls.callables = dict()

        for name in inspect.getattr_static(cls, "__abstractmethods__"):
            attr = inspect.getattr_static(cls, name)
            if callable(attr):
                cls.callables[name] = attr
            elif isinstance(attr, property):
                cls.callables["get_" + name] = build_getter(attr)
                cls.callables["set_" + name] = build_setter(attr)

    def __init__(self, uuid=None, labels={}):
        super().__init__()

        self.uuid = uuid or uuid4()
        self.labels = labels

    @property
    @abstractmethod
    def interface(self): ...

    def call(self, method: str, args: List[Any]) -> Any:
        function = self.callables.get(method)

        if not function:
            raise NotImplementedError("no such drivercall")

        return function(self, *args)

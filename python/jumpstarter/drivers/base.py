# This file contains the base class for all jumpstarter drivers
from abc import ABC, abstractmethod
from typing import List, Any
from dataclasses import dataclass
from uuid import UUID, uuid4
import inspect


# decorator to mark a method available for driver calls
def drivercall(func):
    func.is_drivercall = True
    return func


def is_drivercall(func):
    return getattr(func, "is_drivercall", False)


# base class for all drivers
@dataclass
class DriverBase(ABC):
    uuid: UUID
    labels: dict[str, str]

    def __init_subclass__(cls, **kwargs):
        def build_getter(prop):
            return drivercall(lambda self: prop.fget(self))

        def build_setter(prop):
            return drivercall(lambda self, x: prop.fset(self, x))

        properties = inspect.getmembers_static(
            cls,
            lambda m: isinstance(m, property),
        )
        for name, prop in properties:
            if prop.fget and is_drivercall(prop.fget):
                setattr(cls, "get_" + name, build_getter(prop))
            if prop.fset and is_drivercall(prop.fset):
                setattr(cls, "set_" + name, build_setter(prop))



    def __init__(self, uuid=None, labels={}):
        super().__init__()

        self.uuid = uuid or uuid4()
        self.labels = labels

    @property
    @abstractmethod
    def interface(self): ...

    def call(self, method: str, args: List[Any]) -> Any:
        try:
            function = getattr(self, method)
        except AttributeError:
            raise NotImplementedError("no such drivercall")

        if not is_drivercall(function):
            raise NotImplementedError("no such drivercall")

        return function(*args)

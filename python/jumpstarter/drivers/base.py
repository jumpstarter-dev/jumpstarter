# This file contains the base class for all jumpstarter drivers
from abc import ABC, abstractmethod
from typing import List, Any
from dataclasses import dataclass
from uuid import UUID, uuid4


# decorator to mark a method available for driver calls
def drivercall(func):
    func.is_drivercall = True
    return func


# base class for all drivers
@dataclass
class DriverBase(ABC):
    uuid: UUID
    labels: dict[str, str]

    def __init__(self, uuid=None, labels={}):
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

        if not getattr(function, "is_drivercall", False):
            raise NotImplementedError("no such drivercall")

        return function(*args)

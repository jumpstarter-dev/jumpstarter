# This file contains the base class for all jumpstarter drivers
from abc import ABC
from dataclasses import dataclass
from uuid import UUID, uuid4


@dataclass
class DriverBase(ABC):
    uuid: UUID
    labels: dict[str, str]

    def __init__(self, uuid=None, labels={}):
        self.uuid = uuid or uuid4()
        self.labels = labels

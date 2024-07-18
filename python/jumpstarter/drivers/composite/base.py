from jumpstarter.v1 import jumpstarter_pb2
from dataclasses import dataclass
from typing import List
from uuid import UUID
from .. import DriverBase
import itertools


@dataclass(kw_only=True)
class Composite(DriverBase, interface="composite"):
    devices: List[DriverBase]

    def mapping(self) -> dict[UUID, DriverBase]:
        return super().mapping() | {
            k: v
            for d in [instance.mapping() for instance in self.devices]
            for k, v in d.items()
        }

    def reports(self, parent=None) -> List[jumpstarter_pb2.DriverInstanceReport]:
        return super().reports(parent) + list(
            itertools.chain(*[device.reports(parent=self) for device in self.devices])
        )

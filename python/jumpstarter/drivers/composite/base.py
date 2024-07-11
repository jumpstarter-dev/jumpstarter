from typing import List
from dataclasses import dataclass
from jumpstarter.v1 import jumpstarter_pb2
from .. import DriverBase
import itertools


@dataclass(kw_only=True)
class Composite(DriverBase, interface="composite"):
    devices: List[DriverBase]

    def reports(self, parent=None) -> List[jumpstarter_pb2.DeviceReport]:
        return super().reports(parent) + list(
            itertools.chain(*[device.reports(parent=self) for device in self.devices])
        )

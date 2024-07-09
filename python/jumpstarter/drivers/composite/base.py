from typing import List
from dataclasses import dataclass
from jumpstarter.v1 import jumpstarter_pb2
from .. import DriverBase


@dataclass(kw_only=True)
class Composite(DriverBase, interface="composite"):
    devices: List[DriverBase]

    def reports(self) -> List[jumpstarter_pb2.DeviceReport]:
        return super().reports() + [
            jumpstarter_pb2.DeviceReport(
                parent_device_uuid=str(self.uuid),
                device_uuid=str(device.uuid),
                driver_interface=device.interface,
                labels=device.labels,
            )
            for device in self.devices
        ]

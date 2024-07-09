from typing import List
from dataclasses import dataclass
from .. import DriverBase


@dataclass(kw_only=True)
class Composite(DriverBase, interface="composite"):
    devices: List[DriverBase]

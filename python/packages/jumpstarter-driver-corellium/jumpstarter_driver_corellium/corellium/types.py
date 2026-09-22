"""
Corellium API types.
"""
from dataclasses import dataclass, field


@dataclass
class Project:
    """
    Dataclass that represents a Corellium project.
    """
    id: str
    name: str


@dataclass
class Device:
    """
    Dataclass to represent a Corellium Device.

    A device object is used to create virtual instances.
    """
    name: str
    type: str
    flavor: str
    description: str
    model: str
    peripherals: bool
    quotas: dict


@dataclass
class Instance:
    """
    Virtual instance dataclass.
    """
    id: str
    state: str | None = field(default=None)

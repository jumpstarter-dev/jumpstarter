"""
Corellium API types.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Session:
    """
    Session data class to hold Corellium's API session data.
    """
    token: str
    expiration: str

    def as_header(self) -> Dict[str, str]:
        """
        Return a dict to be used as HTTP header for authenticated requests.
        """
        return {
            'Authorization': f'Bearer {self.token}'
        }


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
    state: Optional[str] = field(default=None)

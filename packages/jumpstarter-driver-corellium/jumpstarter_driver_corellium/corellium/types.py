"""
Corellium API types.
"""

from dataclasses import dataclass
from typing import Dict

from corellium_api import Instance, Model, Project

__all__ = ["Project", "Device", "Instance"]

Device = Model


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
        return {"Authorization": f"Bearer {self.token}"}

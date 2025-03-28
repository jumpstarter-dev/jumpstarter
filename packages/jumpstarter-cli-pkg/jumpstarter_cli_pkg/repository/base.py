from abc import ABC, abstractmethod

from .package import (
    V1Alpha1DriverPackage,
    V1Alpha1DriverPackageList,
)


class DriverRepository(ABC):
    """
    A repository of Jumpstarter plugin packages.
    """

    @abstractmethod
    def list_packages(self) -> V1Alpha1DriverPackageList:
        """
        List all available packages.
        """
        pass

    @abstractmethod
    def get_package(self, name: str) -> V1Alpha1DriverPackage:
        """
        Get a package by name.
        """
        pass

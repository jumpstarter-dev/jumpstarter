from importlib.metadata import Distribution, PackageNotFoundError, distribution, distributions

from .base import DriverRepository
from .entry_points import EntryPointGroups
from .package import (
    V1Alpha1DriverPackage,
    V1Alpha1DriverPackageList,
)
from jumpstarter.common.exceptions import JumpstarterException


class LocalDriverRepository(DriverRepository):
    """
    A local repository of driver packages from the current venv.
    """

    @staticmethod
    def from_venv():
        """
        Create a `LocalDriverRepository` from the current venv.
        """
        return LocalDriverRepository()

    def _is_jumpstarter_package(self, dist: Distribution) -> bool:
        for entry_point in list(dist.entry_points):
            match entry_point.group:
                case (
                    EntryPointGroups.DRIVER_ENTRY_POINT_GROUP
                    | EntryPointGroups.DRIVER_CLIENT_ENTRY_POINT_GROUP
                    | EntryPointGroups.ADAPTER_ENTRY_POINT_GROUP
                ):
                    return True
                case _:
                    return False
        return False

    def list_packages(self, should_inspect: bool = False) -> V1Alpha1DriverPackageList:
        # Get the local drivers using the Jumpstarter drivers entry point
        driver_packages = []
        # Iterate through the local package distributions
        for dist in distributions():
            # Check if the distribution is a Jumpstarter package
            if self._is_jumpstarter_package(dist):
                driver_packages.append(V1Alpha1DriverPackage.from_distribution(dist, should_inspect))
        return V1Alpha1DriverPackageList(items=driver_packages)

    def get_package(self, name: str, should_inspect: bool = False) -> V1Alpha1DriverPackage:
        try:
            # Convert the distribution to a driver package object
            return V1Alpha1DriverPackage.from_distribution(distribution(name), should_inspect)
        except PackageNotFoundError as e:
            raise JumpstarterException(f"Package '{name}' metadata could not be found") from e

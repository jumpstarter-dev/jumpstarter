from importlib.metadata import Distribution, EntryPoint
from typing import Literal, Optional

from pydantic import Field

from .entry_points import EntryPointGroups
from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.models import JsonBaseModel, ListBaseModel


class V1Alpha1AdapterEntryPoint(JsonBaseModel):
    """
    A Jumpstarter adapter entry point.
    """

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["AdapterEntryPoint"] = Field(default="AdapterEntryPoint")

    name: str
    type: str
    package: str

    @staticmethod
    def from_entry_point(ep: EntryPoint):
        return V1Alpha1AdapterEntryPoint(name=ep.name, type=ep.value.replace(":", "."), package=ep.dist.name)


class V1Alpha1DriverClientEntryPoint(JsonBaseModel):
    """
    A Jumpstarter driver client entry point.
    """

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["DriverClientEntryPoint"] = Field(default="DriverClientEntryPoint")

    name: str
    type: str
    package: str

    @staticmethod
    def from_entry_point(ep: EntryPoint):
        return V1Alpha1DriverClientEntryPoint(name=ep.name, type=ep.value.replace(":", "."), package=ep.dist.name)


class V1Alpha1DriverEntryPoint(JsonBaseModel):
    """
    A Jumpstarter driver entry point.
    """

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["DriverEntryPoint"] = Field(default="DriverEntryPoint")

    name: str
    type: str
    package: str

    @staticmethod
    def from_entry_point(ep: EntryPoint):
        return V1Alpha1DriverEntryPoint(name=ep.name, type=ep.value.replace(":", "."), package=ep.dist.name)


class V1Alpha1AdapterEntryPointList(ListBaseModel[V1Alpha1AdapterEntryPoint]):
    """
    A list of Jumpstarter adapter list models.
    """

    kind: Literal["AdapterEntryPointList"] = Field(default="AdapterEntryPointList")


class V1Alpha1DriverEntryPointList(ListBaseModel[V1Alpha1DriverEntryPoint]):
    """
    A list of Jumpstarter driver list models.
    """

    kind: Literal["DriverEntryPointList"] = Field(default="DriverEntryPointList")


class V1Alpha1DriverClientEntryPointList(ListBaseModel[V1Alpha1DriverClientEntryPoint]):
    """
    A list of Jumpstarter driver client classes.
    """

    kind: Literal["DriverClientEntryPointList"] = Field(default="DriverClientEntryPointList")


class V1Alpha1DriverPackage(JsonBaseModel):
    """
    A Jumpstarter driver package.
    """

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1", alias="apiVersion")
    kind: Literal["DriverPackage"] = Field(default="DriverPackage")
    name: str
    version: str
    categories: list[str] = []
    summary: Optional[str] = None
    license: Optional[str] = None
    installed: bool = True

    driver_clients: V1Alpha1DriverClientEntryPointList = Field(
        alias="driverClients", default=V1Alpha1DriverClientEntryPointList(items=[])
    )
    drivers: V1Alpha1DriverEntryPointList = V1Alpha1DriverEntryPointList(items=[])
    adapters: V1Alpha1AdapterEntryPointList = V1Alpha1AdapterEntryPointList(items=[])

    @staticmethod
    def requires_dist_to_categories(name: str, requires_dist: list[str]) -> list[str]:
        """
        Convert the `Requires-Dist` metadata to Jumpstarter driver categories.
        """
        categories = []

        # Define constants for root driver package names
        DRIVER_COMPOSITE = "jumpstarter-driver-composite"
        DRIVER_NETWORK = "jumpstarter-driver-network"
        DRIVER_OPENDAL = "jumpstarter-driver-storage"
        DRIVER_POWER = "jumpstarter-driver-power"

        # Define constants for category names
        CATEGORY_COMPOSITE = "composite"
        CATEGORY_NETWORK = "network"
        CATEGORY_STORAGE = "storage"
        CATEGORY_POWER = "power"

        # Check package name
        if name == DRIVER_COMPOSITE:
            categories.append(CATEGORY_COMPOSITE)
        elif name == DRIVER_NETWORK:
            categories.append(CATEGORY_NETWORK)
        elif name == DRIVER_OPENDAL:
            categories.append(CATEGORY_STORAGE)
        elif name == DRIVER_POWER:
            categories.append(CATEGORY_POWER)

        # Check package dependencies
        for dist in requires_dist:
            if DRIVER_COMPOSITE in dist and CATEGORY_COMPOSITE not in categories:
                categories.append(CATEGORY_COMPOSITE)
            elif DRIVER_NETWORK in dist and CATEGORY_NETWORK not in categories:
                categories.append(CATEGORY_NETWORK)
            elif DRIVER_OPENDAL in dist and CATEGORY_STORAGE not in categories:
                categories.append(CATEGORY_STORAGE)
            elif DRIVER_POWER in dist and CATEGORY_POWER not in categories:
                categories.append(CATEGORY_POWER)

        return categories

    @staticmethod
    def from_distribution(dist: Distribution):
        """
        Create a `DriverPackage` from an `importlib.metadata.EntryPoint`.
        """
        # Collect entry points for each type
        drivers = []
        driver_clients = []
        adapters = []
        # Iterate through the entry points
        for ep in list(dist.entry_points):
            match ep.group:
                case EntryPointGroups.DRIVER_ENTRY_POINT_GROUP:
                    drivers.append(V1Alpha1DriverEntryPoint.from_entry_point(ep))
                case EntryPointGroups.DRIVER_CLIENT_ENTRY_POINT_GROUP:
                    driver_clients.append(V1Alpha1DriverClientEntryPoint.from_entry_point(ep))
                case EntryPointGroups.ADAPTER_ENTRY_POINT_GROUP:
                    adapters.append(V1Alpha1AdapterEntryPoint.from_entry_point(ep))
        #  Check if any entry points were found
        if len(drivers) + len(driver_clients) + len(adapters) == 0:
            raise JumpstarterException(f"No valid Jumpstarter entry points found for package '{dist.name}'")
        # Return the completed driver package
        return V1Alpha1DriverPackage(
            name=dist.name,
            categories=V1Alpha1DriverPackage.requires_dist_to_categories(
                dist.name, dist.metadata.get_all("Requires-Dist")
            ),
            version=dist.version,
            summary=dist.metadata.get("Summary"),
            license=dist.metadata.get("License"),
            drivers=V1Alpha1DriverEntryPointList(items=drivers),
            driver_clients=V1Alpha1DriverClientEntryPointList(items=driver_clients),
            adapters=V1Alpha1AdapterEntryPointList(items=adapters),
        )

    def list_drivers(self) -> V1Alpha1DriverEntryPointList:
        return self.drivers

    def list_driver_clients(self) -> V1Alpha1DriverClientEntryPointList:
        return self.driver_clients

    def list_adapters(self) -> V1Alpha1AdapterEntryPointList:
        return self.adapters


class V1Alpha1DriverPackageList(ListBaseModel[V1Alpha1DriverPackage]):
    """
    A list of Jumpstarter driver packages.
    """

    kind: Literal["DriverPackageList"] = Field(default="DriverPackageList")

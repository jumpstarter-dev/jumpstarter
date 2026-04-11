from typing import Literal

from kubernetes_asyncio.client.models import V1ObjectMeta
from pydantic import Field

from .datetime import time_since
from .json import JsonBaseModel
from .list import V1Alpha1List
from .serialize import SerializeV1Condition, SerializeV1ObjectMeta
from .util import AbstractAsyncCustomObjectApi

GROUP = "jumpstarter.dev"
VERSION = "v1alpha1"
PLURAL = "driverinterfaces"


class V1Alpha1DriverInterfaceProto(JsonBaseModel):
    package: str
    descriptor: str | None = None


class V1Alpha1DriverImplementation(JsonBaseModel):
    language: str
    package: str
    version: str | None = None
    index: str | None = None
    client_class: str | None = Field(alias="clientClass", default=None)
    driver_classes: list[str] | None = Field(alias="driverClasses", default=None)


class V1Alpha1DriverInterfaceSpec(JsonBaseModel):
    proto: V1Alpha1DriverInterfaceProto
    drivers: list[V1Alpha1DriverImplementation] | None = None


class V1Alpha1DriverInterfaceStatus(JsonBaseModel):
    implementation_count: int = Field(alias="implementationCount", default=0)
    conditions: list[SerializeV1Condition] | None = None


class V1Alpha1DriverInterface(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["DriverInterface"] = Field(default="DriverInterface")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1DriverInterfaceSpec | None = None
    status: V1Alpha1DriverInterfaceStatus | None = None

    @staticmethod
    def from_dict(d: dict):
        spec_data = d.get("spec", {})
        status_data = d.get("status", {})
        return V1Alpha1DriverInterface(
            api_version=d.get("apiVersion", "jumpstarter.dev/v1alpha1"),
            kind=d.get("kind", "DriverInterface"),
            metadata=V1ObjectMeta(
                creation_timestamp=d["metadata"].get("creationTimestamp"),
                generation=d["metadata"].get("generation"),
                name=d["metadata"].get("name"),
                namespace=d["metadata"].get("namespace"),
                resource_version=d["metadata"].get("resourceVersion"),
                uid=d["metadata"].get("uid"),
            ),
            spec=V1Alpha1DriverInterfaceSpec(
                proto=V1Alpha1DriverInterfaceProto(
                    package=spec_data.get("proto", {}).get("package", ""),
                    descriptor=spec_data.get("proto", {}).get("descriptor"),
                ),
                drivers=[
                    V1Alpha1DriverImplementation(
                        language=drv.get("language", ""),
                        package=drv.get("package", ""),
                        version=drv.get("version"),
                        index=drv.get("index"),
                        clientClass=drv.get("clientClass"),
                        driverClasses=drv.get("driverClasses"),
                    )
                    for drv in spec_data.get("drivers", [])
                ]
                if spec_data.get("drivers")
                else None,
            )
            if spec_data
            else None,
            status=V1Alpha1DriverInterfaceStatus(
                implementationCount=status_data.get("implementationCount", 0),
                conditions=status_data.get("conditions"),
            )
            if status_data
            else None,
        )

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        table.add_column("NAME", no_wrap=True)
        table.add_column("PACKAGE")
        table.add_column("IMPLEMENTATIONS")
        table.add_column("AGE")

    def rich_add_rows(self, table, **kwargs):
        impl_count = str(self.status.implementation_count) if self.status else "0"
        table.add_row(
            self.metadata.name,
            self.spec.proto.package if self.spec else "",
            impl_count,
            time_since(self.metadata.creation_timestamp) if self.metadata.creation_timestamp else "",
        )

    def rich_add_names(self, names):
        names.append(f"driverinterface.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1DriverInterfaceList(V1Alpha1List[V1Alpha1DriverInterface]):
    kind: Literal["DriverInterfaceList"] = Field(default="DriverInterfaceList")

    @staticmethod
    def from_dict(d: dict):
        return V1Alpha1DriverInterfaceList(items=[V1Alpha1DriverInterface.from_dict(i) for i in d["items"]])

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1DriverInterface.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for item in self.items:
            item.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for item in self.items:
            item.rich_add_names(names)


class DriverInterfacesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the driverinterfaces custom resource API"""

    async def list_driver_interfaces(self) -> V1Alpha1DriverInterfaceList:
        """List DriverInterface objects in the namespace"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION
        )
        return V1Alpha1DriverInterfaceList.from_dict(res)

    async def get_driver_interface(self, name: str) -> V1Alpha1DriverInterface:
        """Get a single DriverInterface object"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
        )
        return V1Alpha1DriverInterface.from_dict(result)

    async def apply_driver_interface(self, body: dict) -> V1Alpha1DriverInterface:
        """Create or update a DriverInterface in the cluster"""
        name = body["metadata"]["name"]
        try:
            # Try to get existing resource
            existing = await self.api.get_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
            )
            # Update: set resourceVersion for optimistic concurrency
            body["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
            result = await self.api.replace_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name, body=body
            )
        except Exception:
            # Create new resource
            result = await self.api.create_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, body=body
            )
        return V1Alpha1DriverInterface.from_dict(result)

    async def delete_driver_interface(self, name: str):
        """Delete a DriverInterface object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
        )

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
PLURAL = "exporterclasses"


class V1Alpha1InterfaceRequirement(JsonBaseModel):
    name: str
    interface_ref: str = Field(alias="interfaceRef")
    required: bool = True


class V1Alpha1LabelSelector(JsonBaseModel):
    match_labels: dict[str, str] | None = Field(alias="matchLabels", default=None)
    match_expressions: list[dict] | None = Field(alias="matchExpressions", default=None)


class V1Alpha1ExporterClassSpec(JsonBaseModel):
    extends: str | None = None
    selector: V1Alpha1LabelSelector | None = None
    interfaces: list[V1Alpha1InterfaceRequirement] | None = None


class V1Alpha1ExporterClassStatus(JsonBaseModel):
    satisfied_exporter_count: int = Field(alias="satisfiedExporterCount", default=0)
    resolved_interfaces: list[str] | None = Field(alias="resolvedInterfaces", default=None)
    conditions: list[SerializeV1Condition] | None = None


class V1Alpha1ExporterClass(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["ExporterClass"] = Field(default="ExporterClass")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1ExporterClassSpec | None = None
    status: V1Alpha1ExporterClassStatus | None = None

    @staticmethod
    def from_dict(d: dict):
        spec_data = d.get("spec", {})
        status_data = d.get("status", {})
        return V1Alpha1ExporterClass(
            api_version=d.get("apiVersion", "jumpstarter.dev/v1alpha1"),
            kind=d.get("kind", "ExporterClass"),
            metadata=V1ObjectMeta(
                creation_timestamp=d["metadata"].get("creationTimestamp"),
                generation=d["metadata"].get("generation"),
                name=d["metadata"].get("name"),
                namespace=d["metadata"].get("namespace"),
                resource_version=d["metadata"].get("resourceVersion"),
                uid=d["metadata"].get("uid"),
            ),
            spec=V1Alpha1ExporterClassSpec(
                extends=spec_data.get("extends"),
                selector=V1Alpha1LabelSelector(
                    matchLabels=spec_data["selector"].get("matchLabels"),
                    matchExpressions=spec_data["selector"].get("matchExpressions"),
                )
                if spec_data.get("selector")
                else None,
                interfaces=[
                    V1Alpha1InterfaceRequirement(
                        name=iface.get("name", ""),
                        interfaceRef=iface.get("interfaceRef", ""),
                        required=iface.get("required", True),
                    )
                    for iface in spec_data.get("interfaces", [])
                ]
                if spec_data.get("interfaces")
                else None,
            )
            if spec_data
            else None,
            status=V1Alpha1ExporterClassStatus(
                satisfiedExporterCount=status_data.get("satisfiedExporterCount", 0),
                resolvedInterfaces=status_data.get("resolvedInterfaces"),
                conditions=status_data.get("conditions"),
            )
            if status_data
            else None,
        )

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        table.add_column("NAME", no_wrap=True)
        table.add_column("EXTENDS")
        table.add_column("INTERFACES")
        table.add_column("SATISFIED")
        table.add_column("AGE")

    def rich_add_rows(self, table, **kwargs):
        extends = self.spec.extends or "" if self.spec else ""
        iface_count = str(len(self.spec.interfaces)) if self.spec and self.spec.interfaces else "0"
        satisfied = str(self.status.satisfied_exporter_count) if self.status else "0"
        table.add_row(
            self.metadata.name,
            extends,
            iface_count,
            satisfied,
            time_since(self.metadata.creation_timestamp) if self.metadata.creation_timestamp else "",
        )

    def rich_add_names(self, names):
        names.append(f"exporterclass.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1ExporterClassList(V1Alpha1List[V1Alpha1ExporterClass]):
    kind: Literal["ExporterClassList"] = Field(default="ExporterClassList")

    @staticmethod
    def from_dict(d: dict):
        return V1Alpha1ExporterClassList(items=[V1Alpha1ExporterClass.from_dict(i) for i in d["items"]])

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1ExporterClass.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for item in self.items:
            item.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for item in self.items:
            item.rich_add_names(names)


class ExporterClassesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the exporterclasses custom resource API"""

    async def list_exporter_classes(self) -> V1Alpha1ExporterClassList:
        """List ExporterClass objects in the namespace"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION
        )
        return V1Alpha1ExporterClassList.from_dict(res)

    async def get_exporter_class(self, name: str) -> V1Alpha1ExporterClass:
        """Get a single ExporterClass object"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
        )
        return V1Alpha1ExporterClass.from_dict(result)

    async def apply_exporter_class(self, body: dict) -> V1Alpha1ExporterClass:
        """Create or update an ExporterClass in the cluster"""
        name = body["metadata"]["name"]
        try:
            existing = await self.api.get_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
            )
            body["metadata"]["resourceVersion"] = existing["metadata"]["resourceVersion"]
            result = await self.api.replace_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name, body=body
            )
        except Exception:
            result = await self.api.create_namespaced_custom_object(
                namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, body=body
            )
        return V1Alpha1ExporterClass.from_dict(result)

    async def delete_exporter_class(self, name: str):
        """Delete an ExporterClass object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace, group=GROUP, plural=PLURAL, version=VERSION, name=name
        )

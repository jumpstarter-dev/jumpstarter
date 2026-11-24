from typing import Literal

from kubernetes_asyncio.client.models import V1ObjectMeta
from pydantic import Field

from .datetime import time_since
from .json import JsonBaseModel, OmitNoneModel
from .leases import V1Alpha1LeaseConstraint, V1Alpha1LeaseForward, V1Alpha1LeaseMember
from .list import V1Alpha1List
from .serialize import SerializeV1ObjectMeta
from .util import AbstractAsyncCustomObjectApi

DESCRIPTION_WIDTH = 60


class V1Alpha1LeaseTemplateDefaults(OmitNoneModel):
    duration: str | None = None
    shared_with: list[str] | None = Field(alias="sharedWith", default=None)


class V1Alpha1LeaseTemplateSpec(OmitNoneModel):
    description: str | None = None
    docs: list[str] | None = None
    image: str | None = None
    instructions: str | None = None
    members: list[V1Alpha1LeaseMember]
    constraints: list[V1Alpha1LeaseConstraint] | None = None
    # What connects the members of a lease from this template.
    forwards: list[V1Alpha1LeaseForward] | None = None
    defaults: V1Alpha1LeaseTemplateDefaults | None = None


class V1Alpha1LeaseTemplate(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["LeaseTemplate"] = Field(default="LeaseTemplate")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1LeaseTemplateSpec

    @staticmethod
    def from_dict(data: dict):
        metadata = data["metadata"]
        return V1Alpha1LeaseTemplate(
            apiVersion=data["apiVersion"],
            kind=data["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=metadata.get("creationTimestamp"),
                # A lease records the generation of the template it was made from.
                generation=metadata.get("generation"),
                labels=metadata.get("labels"),
                name=metadata["name"],
                namespace=metadata.get("namespace"),
                resource_version=metadata.get("resourceVersion"),
                uid=metadata.get("uid"),
            ),
            spec=V1Alpha1LeaseTemplateSpec.model_validate(data["spec"]),
        )

    def members_summary(self) -> str:
        """The role names, an optional role marked with ``*``."""
        return ", ".join(f"{member.name}*" if member.optional else member.name for member in self.spec.members)

    def description_summary(self) -> str:
        description = " ".join((self.spec.description or "").split())
        if len(description) > DESCRIPTION_WIDTH:
            return description[: DESCRIPTION_WIDTH - 1] + "…"
        return description

    def has_forwards(self) -> bool:
        return bool(self.spec.forwards)

    def forwards_summary(self) -> str:
        """Each forward with its endpoints: ``name(member.port,member.port)``."""
        return " ".join(forward.summary() for forward in self.spec.forwards or [])

    @classmethod
    def rich_add_columns(cls, table, forwards: bool = False):
        table.add_column("NAME", no_wrap=True)
        table.add_column("MEMBERS")
        # The FORWARDS column appears only where a template has forwards.
        if forwards:
            table.add_column("FORWARDS")
        table.add_column("DURATION")
        table.add_column("DESCRIPTION")
        table.add_column("AGE")

    def rich_add_rows(self, table, forwards: bool = False):
        defaults = self.spec.defaults
        table.add_row(
            self.metadata.name,
            self.members_summary(),
            *([self.forwards_summary()] if forwards else []),
            (defaults.duration if defaults is not None else None) or "",
            self.description_summary(),
            time_since(self.metadata.creation_timestamp) if self.metadata.creation_timestamp else "",
        )

    def rich_add_names(self, names):
        names.append(f"leasetemplate.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1LeaseTemplateList(V1Alpha1List[V1Alpha1LeaseTemplate]):
    kind: Literal["LeaseTemplateList"] = Field(default="LeaseTemplateList", frozen=True)

    @staticmethod
    def from_dict(data: dict):
        return V1Alpha1LeaseTemplateList(items=[V1Alpha1LeaseTemplate.from_dict(t) for t in data.get("items", [])])

    def has_forwards(self) -> bool:
        return any(template.has_forwards() for template in self.items)

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1LeaseTemplate.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for template in self.items:
            template.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for template in self.items:
            template.rich_add_names(names)


class LeaseTemplatesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the lease templates custom resource API

    Templates are written with ``ApplyV1Alpha1Api``, like every other manifest.
    """

    async def list_lease_templates(self) -> V1Alpha1LeaseTemplateList:
        """List the lease template objects in the cluster"""
        result = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leasetemplates", version="v1alpha1"
        )
        return V1Alpha1LeaseTemplateList.from_dict(result)

    async def get_lease_template(self, name: str) -> V1Alpha1LeaseTemplate:
        """Get a single lease template object from the cluster"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leasetemplates", version="v1alpha1", name=name
        )
        return V1Alpha1LeaseTemplate.from_dict(result)

    async def delete_lease_template(self, name: str):
        """Delete a lease template object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace,
            name=name,
            group="jumpstarter.dev",
            plural="leasetemplates",
            version="v1alpha1",
        )

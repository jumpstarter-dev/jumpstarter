from typing import Literal

from kubernetes_asyncio.client.models import V1ObjectMeta
from pydantic import Field

from .datetime import time_since
from .json import JsonBaseModel, OmitNoneModel
from .list import V1Alpha1List
from .serialize import SerializeV1ObjectMeta
from .util import AbstractAsyncCustomObjectApi


class V1Alpha1ClaimLeaseReference(JsonBaseModel):
    name: str
    uid: str
    attempt: int


class V1Alpha1ExporterClaimSpec(OmitNoneModel):
    """What a lease holds: one exporter, or one exclusion group."""

    exporter: str | None = None
    exclusion_group: str | None = Field(alias="exclusionGroup", default=None)
    lease_ref: V1Alpha1ClaimLeaseReference = Field(alias="leaseRef")


class V1Alpha1ExporterClaim(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["ExporterClaim"] = Field(default="ExporterClaim")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1ExporterClaimSpec

    @staticmethod
    def from_dict(data: dict):
        metadata = data["metadata"]
        return V1Alpha1ExporterClaim(
            apiVersion=data["apiVersion"],
            kind=data["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=metadata.get("creationTimestamp"),
                generation=metadata.get("generation"),
                name=metadata["name"],
                namespace=metadata.get("namespace"),
                resource_version=metadata.get("resourceVersion"),
                uid=metadata.get("uid"),
            ),
            spec=V1Alpha1ExporterClaimSpec.model_validate(data["spec"]),
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
        table.add_column("EXPORTER")
        table.add_column("GROUP")
        table.add_column("LEASE")
        table.add_column("ATTEMPT")
        table.add_column("AGE")

    def rich_add_rows(self, table):
        table.add_row(
            self.metadata.name,
            self.spec.exporter or "",
            self.spec.exclusion_group or "",
            self.spec.lease_ref.name,
            str(self.spec.lease_ref.attempt),
            time_since(self.metadata.creation_timestamp) if self.metadata.creation_timestamp else "",
        )

    def rich_add_names(self, names):
        names.append(f"exporterclaim.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1ExporterClaimList(V1Alpha1List[V1Alpha1ExporterClaim]):
    kind: Literal["ExporterClaimList"] = Field(default="ExporterClaimList", frozen=True)

    @staticmethod
    def from_dict(data: dict):
        return V1Alpha1ExporterClaimList(items=[V1Alpha1ExporterClaim.from_dict(c) for c in data.get("items", [])])

    @classmethod
    def rich_add_columns(cls, table):
        V1Alpha1ExporterClaim.rich_add_columns(table)

    def rich_add_rows(self, table):
        for claim in self.items:
            claim.rich_add_rows(table)

    def rich_add_names(self, names):
        for claim in self.items:
            claim.rich_add_names(names)


class ExporterClaimsV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Read the exporter claims custom resource API. Only the controller writes claims."""

    async def list_exporter_claims(self) -> V1Alpha1ExporterClaimList:
        """List the exporter claim objects in the cluster"""
        result = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporterclaims", version="v1alpha1"
        )
        return V1Alpha1ExporterClaimList.from_dict(result)

    async def get_exporter_claim(self, name: str) -> V1Alpha1ExporterClaim:
        """Get a single exporter claim object from the cluster"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporterclaims", version="v1alpha1", name=name
        )
        return V1Alpha1ExporterClaim.from_dict(result)

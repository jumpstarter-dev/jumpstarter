from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference
from pydantic import Field

from .serialize import SerializeV1Condition, SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi
from jumpstarter.models import JsonBaseModel, ListBaseModel


class V1Alpha1LeaseStatus(JsonBaseModel):
    begin_time: Optional[str] = Field(alias="beginTime")
    conditions: list[SerializeV1Condition]
    end_time: Optional[str] = Field(alias="endTime")
    ended: bool
    exporter: Optional[SerializeV1ObjectReference]


class V1Alpha1LeaseSelector(JsonBaseModel):
    match_labels: dict[str, str] = Field(alias="matchLabels")


class V1Alpha1LeaseSpec(JsonBaseModel):
    client: SerializeV1ObjectReference
    duration: Optional[str]
    selector: V1Alpha1LeaseSelector


class V1Alpha1Lease(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Lease"] = Field(default="Lease")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1LeaseSpec
    status: V1Alpha1LeaseStatus

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1Lease(
            api_version=dict["apiVersion"],
            kind=dict["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=dict["metadata"]["creationTimestamp"],
                generation=dict["metadata"]["generation"],
                managed_fields=dict["metadata"]["managedFields"],
                name=dict["metadata"]["name"],
                namespace=dict["metadata"]["namespace"],
                resource_version=dict["metadata"]["resourceVersion"],
                uid=dict["metadata"]["uid"],
            ),
            status=V1Alpha1LeaseStatus(
                begin_time=dict["status"]["beginTime"] if "beginTime" in dict["status"] else None,
                end_time=dict["status"]["endTime"] if "endTime" in dict["status"] else None,
                ended=dict["status"]["ended"],
                exporter=V1ObjectReference(name=dict["status"]["exporterRef"]["name"])
                if "exporterRef" in dict["status"]
                else None,
                conditions=[
                    V1Condition(
                        last_transition_time=cond["lastTransitionTime"],
                        message=cond["message"],
                        observed_generation=cond["observedGeneration"],
                        reason=cond["reason"],
                        status=cond["status"],
                        type=cond["type"],
                    )
                    for cond in dict["status"]["conditions"]
                ],
            ),
            spec=V1Alpha1LeaseSpec(
                client=V1ObjectReference(name=dict["spec"]["clientRef"]["name"])
                if "clientRef" in dict["spec"]
                else None,
                duration=dict["spec"]["duration"] if "duration" in dict["spec"] else None,
                selector=V1Alpha1LeaseSelector(match_labels=dict["spec"]["selector"]["matchLabels"]),
            ),
        )


class V1Alpha1LeaseList(ListBaseModel[V1Alpha1Lease]):
    kind: Literal["LeaseList"] = Field(default="LeaseList")

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1LeaseList(items=[V1Alpha1Lease.from_dict(c) for c in dict["items"]])


class LeasesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the leases custom resource API"""

    async def list_leases(self) -> V1Alpha1LeaseList:
        """List the lease objects in the cluster async"""
        result = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1"
        )
        return V1Alpha1LeaseList.from_dict(result)

    async def get_lease(self, name: str) -> V1Alpha1Lease:
        """Get a single lease object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1", name=name
        )
        return V1Alpha1Lease.from_dict(result)

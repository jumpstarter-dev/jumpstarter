import pprint
from typing import Literal, Optional

import yaml
from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference
from pydantic import BaseModel, ConfigDict, Field

from .serialize import SerializeV1Condition, SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi


class V1Alpha1LeaseStatus(BaseModel):
    begin_time: str
    conditions: list[SerializeV1Condition]
    end_time: Optional[str]
    ended: bool
    exporter: Optional[SerializeV1ObjectReference]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class V1Alpha1LeaseSpec(BaseModel):
    client: SerializeV1ObjectReference
    duration: Optional[str]
    selector: dict[str, str]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class V1Alpha1Lease(BaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Lease"] = Field(default="Lease")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1LeaseSpec
    status: V1Alpha1LeaseStatus

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(by_alias=True), indent=2)


class LeasesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the leases custom resource API"""

    @staticmethod
    def _deserialize(result: dict) -> V1Alpha1Lease:
        return V1Alpha1Lease(
            api_version=result["apiVersion"],
            kind=result["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=result["metadata"]["creationTimestamp"],
                generation=result["metadata"]["generation"],
                name=result["metadata"]["name"],
                namespace=result["metadata"]["namespace"],
                resource_version=result["metadata"]["resourceVersion"],
                uid=result["metadata"]["uid"],
            ),
            status=V1Alpha1LeaseStatus(
                begin_time=result["status"]["beginTime"] if "beginTime" in result["status"] else None,
                end_time=result["status"]["endTime"] if "endTime" in result["status"] else None,
                ended=result["status"]["ended"],
                exporter=V1ObjectReference(name=result["status"]["exporterRef"]["name"])
                if "exporterRef" in result["status"]
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
                    for cond in result["status"]["conditions"]
                ],
            ),
            spec=V1Alpha1LeaseSpec(
                client=V1ObjectReference(name=result["spec"]["clientRef"]["name"])
                if "clientRef" in result["spec"]
                else None,
                duration=result["spec"]["duration"] if "duration" in result["spec"] else None,
                selector=result["spec"]["selector"],
            ),
        )

    async def list_leases(self) -> list[V1Alpha1Lease]:
        """List the lease objects in the cluster async"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1"
        )
        return [LeasesV1Alpha1Api._deserialize(c) for c in res["items"]]

    async def get_lease(self, name: str) -> V1Alpha1Lease:
        """Get a single lease object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1", name=name
        )
        pprint.pp(result)
        return LeasesV1Alpha1Api._deserialize(result)

import pprint
from dataclasses import dataclass
from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference

from .util import AbstractAsyncCustomObjectApi


@dataclass(kw_only=True)
class V1Alpha1LeaseStatus:
    begin_time: str
    end_time: Optional[str]
    ended: bool
    exporter: Optional[V1ObjectReference]
    conditions: list[V1Condition]


@dataclass(kw_only=True)
class V1Alpha1LeaseSpec:
    client: V1ObjectReference
    duration: Optional[str]
    selector: dict[str, str]


@dataclass(kw_only=True)
class V1Alpha1Lease:
    api_version: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["Lease"]
    metadata: V1ObjectMeta
    status: V1Alpha1LeaseStatus
    spec: V1Alpha1LeaseSpec


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

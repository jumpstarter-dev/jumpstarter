from dataclasses import dataclass
from typing import Literal

from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from .util import AbstractAsyncCustomObjectApi


@dataclass(kw_only=True)
class V1Alpha1ExporterDevice:
    labels: dict[str, str]
    uuid: str


@dataclass(kw_only=True)
class V1Alpha1ExporterStatus:
    credential: V1ObjectReference
    endpoint: str
    devices: list[V1Alpha1ExporterDevice]


@dataclass(kw_only=True)
class V1Alpha1Exporter:
    api_version: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["Exporter"]
    metadata: V1ObjectMeta
    status: V1Alpha1ExporterStatus


class ExportersV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the exporters custom resource API"""

    @staticmethod
    def _deserialize(result: dict) -> V1Alpha1Exporter:
        return V1Alpha1Exporter(
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
            status=V1Alpha1ExporterStatus(
                credential=V1ObjectReference(name=result["status"]["credential"]["name"])
                if "credential" in result["status"]
                else None,
                endpoint=result["status"]["endpoint"],
                devices=[
                    V1Alpha1ExporterDevice(labels=d["labels"], uuid=d["uuid"]) for d in result["status"]["devices"]
                ]
                if "devices" in result["status"]
                else [],
            ),
        )

    async def list_exporters(self) -> list[V1Alpha1Exporter]:
        """List the exporter objects in the cluster async"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1"
        )
        return [ExportersV1Alpha1Api._deserialize(c) for c in res["items"]]

    async def get_exporter(self, name: str) -> V1Alpha1Exporter:
        """Get a single exporter object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1", name=name
        )
        return ExportersV1Alpha1Api._deserialize(result)

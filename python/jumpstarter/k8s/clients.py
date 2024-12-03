import asyncio
import logging
import pprint
from dataclasses import dataclass
from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from .util import AbstractAsyncCustomObjectApi

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class V1Alpha1ClientStatus:
    credential: Optional[V1ObjectReference] = None
    endpoint: str


@dataclass(kw_only=True)
class V1Alpha1Client:
    api_version: Literal["jumpstarter.dev/v1alpha1"]
    kind: Literal["Client"]
    metadata: V1ObjectMeta
    status: V1Alpha1ClientStatus


class ClientsV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the clients custom resource API"""

    @staticmethod
    def _deserialize(result: dict) -> V1Alpha1Client:
        return V1Alpha1Client(
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
            status=V1Alpha1ClientStatus(
                credential=V1ObjectReference(name=result["status"]["credential"]["name"])
                if "credential" in result["status"]
                else None,
                endpoint=result["status"]["endpoint"],
            ),
        )

    async def create_client(self, name: str) -> V1Alpha1Client:
        """Create a client object in the cluster async"""
        # Create the namespaced client object
        await self.api.create_namespaced_custom_object(
            namespace=self.namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            body={"apiVersion": "jumpstarter.dev/v1alpha1", "kind": "Client", "metadata": {"name": name}},
        )
        # Wait for the credentials to become available
        # NOTE: Watch is not working here with the Python kubernetes library
        count = 0
        updated_client = {}
        # Retry for a maximum of 10s
        while count < 10:
            await asyncio.sleep(1)
            # Try to get the updated client resource
            updated_client = await self.get_client(name)
            pprint.pp(updated_client)
            count += 1
        return ClientsV1Alpha1Api._deserialize(updated_client)

    async def list_clients(self) -> list[V1Alpha1Client]:
        """List the client objects in the cluster async"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1"
        )
        return [ClientsV1Alpha1Api._deserialize(c) for c in res["items"]]

    async def get_client(self, name: str) -> V1Alpha1Client:
        """Get a single client object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1", name=name
        )
        return ClientsV1Alpha1Api._deserialize(result)

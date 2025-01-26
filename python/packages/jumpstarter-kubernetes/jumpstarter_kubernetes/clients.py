import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from jumpstarter.config import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers

from .util import AbstractAsyncCustomObjectApi

logger = logging.getLogger(__name__)

CREATE_CLIENT_DELAY = 1
CREATE_CLIENT_COUNT = 10


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
            )
            if "status" in result
            else V1Alpha1ClientStatus(credential=None, endpoint=""),
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
        while count < CREATE_CLIENT_COUNT:
            # Try to get the updated client resource
            updated_client = await self.api.get_namespaced_custom_object(
                namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1", name=name
            )
            # check if the client status is updated with the credentials
            if "status" in updated_client:
                if "credential" in updated_client["status"]:
                    return ClientsV1Alpha1Api._deserialize(updated_client)
            count += 1
            await asyncio.sleep(CREATE_CLIENT_DELAY)
        raise Exception("Timeout waiting for client credentials")

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

    async def get_client_config(self, name: str, allow: list[str], unsafe=False) -> ClientConfigV1Alpha1:
        """Get a client config for a specified client name"""
        client = await self.get_client(name)
        secret = await self.core_api.read_namespaced_secret(client.status.credential.name, self.namespace)
        endpoint = client.status.endpoint
        token = base64.b64decode(secret.data["token"]).decode("utf8")
        return ClientConfigV1Alpha1(
            name=name,
            endpoint=endpoint,
            token=token,
            drivers=ClientConfigV1Alpha1Drivers(allow=allow, unsafe=unsafe),
        )

    async def delete_client(self, name: str):
        """Delete a client object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1", name=name
        )

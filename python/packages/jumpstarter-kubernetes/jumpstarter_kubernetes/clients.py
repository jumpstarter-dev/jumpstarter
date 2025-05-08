import asyncio
import base64
import logging
from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference
from pydantic import Field

from .json import JsonBaseModel
from .list import V1Alpha1List
from .serialize import SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta

logger = logging.getLogger(__name__)

CREATE_CLIENT_DELAY = 1
CREATE_CLIENT_COUNT = 10


class V1Alpha1ClientStatus(JsonBaseModel):
    credential: Optional[SerializeV1ObjectReference] = None
    endpoint: str


class V1Alpha1Client(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Client"] = Field(default="Client")
    metadata: SerializeV1ObjectMeta
    status: Optional[V1Alpha1ClientStatus]

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1Client(
            api_version=dict["apiVersion"],
            kind=dict["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=dict["metadata"]["creationTimestamp"],
                generation=dict["metadata"]["generation"],
                name=dict["metadata"]["name"],
                namespace=dict["metadata"]["namespace"],
                resource_version=dict["metadata"]["resourceVersion"],
                uid=dict["metadata"]["uid"],
            ),
            status=V1Alpha1ClientStatus(
                credential=V1ObjectReference(name=dict["status"]["credential"]["name"])
                if "credential" in dict["status"]
                else None,
                endpoint=dict["status"].get("endpoint", ""),
            )
            if "status" in dict
            else None,
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME")
        table.add_column("ENDPOINT")
        # table.add_column("AGE")

    def rich_add_rows(self, table):
        table.add_row(
            self.metadata.name,
            self.status.endpoint if self.status is not None else "",
        )

    def rich_add_names(self, names):
        names.append(f"client.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1ClientList(V1Alpha1List[V1Alpha1Client]):
    kind: Literal["ClientList"] = Field(default="ClientList")

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1ClientList(items=[V1Alpha1Client.from_dict(c) for c in dict.get("items", [])])

    @classmethod
    def rich_add_columns(cls, table):
        V1Alpha1Client.rich_add_columns(table)

    def rich_add_rows(self, table):
        for client in self.items:
            client.rich_add_rows(table)

    def rich_add_names(self, names):
        for client in self.items:
            client.rich_add_names(names)


class ClientsV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the clients custom resource API"""

    async def create_client(
        self, name: str, labels: dict[str, str] | None = None, oidc_username: str | None = None
    ) -> V1Alpha1Client:
        """Create a client object in the cluster async"""
        # Create the namespaced client object
        await self.api.create_namespaced_custom_object(
            namespace=self.namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            body={
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Client",
                "metadata": {"name": name} | {"labels": labels} if labels is not None else {},
                "spec": {"username": oidc_username} if oidc_username is not None else {},
            },
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
                    return V1Alpha1Client.from_dict(updated_client)
            count += 1
            await asyncio.sleep(CREATE_CLIENT_DELAY)
        raise Exception("Timeout waiting for client credentials")

    async def list_clients(self) -> V1Alpha1List[V1Alpha1Client]:
        """List the client objects in the cluster async"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1"
        )
        return V1Alpha1ClientList.from_dict(res)

    async def get_client(self, name: str) -> V1Alpha1Client:
        """Get a single client object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1", name=name
        )
        return V1Alpha1Client.from_dict(result)

    async def get_client_config(self, name: str, allow: list[str], unsafe=False) -> ClientConfigV1Alpha1:
        """Get a client config for a specified client name"""
        client = await self.get_client(name)
        secret = await self.core_api.read_namespaced_secret(client.status.credential.name, self.namespace)
        endpoint = client.status.endpoint
        token = base64.b64decode(secret.data["token"]).decode("utf8")
        return ClientConfigV1Alpha1(
            alias=name,
            metadata=ObjectMeta(
                namespace=client.metadata.namespace,
                name=client.metadata.name,
            ),
            endpoint=endpoint,
            token=token,
            drivers=ClientConfigV1Alpha1Drivers(allow=allow, unsafe=unsafe),
        )

    async def delete_client(self, name: str):
        """Delete a client object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="clients", version="v1alpha1", name=name
        )

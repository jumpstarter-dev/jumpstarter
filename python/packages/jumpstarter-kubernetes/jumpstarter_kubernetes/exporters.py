import asyncio
import base64
from typing import Literal

import yaml
from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference
from pydantic import BaseModel, ConfigDict, Field

from .list import V1Alpha1List
from .serialize import SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi
from jumpstarter.config import ExporterConfigV1Alpha1, ObjectMeta

CREATE_EXPORTER_DELAY = 1
CREATE_EXPORTER_COUNT = 10


class V1Alpha1ExporterDevice(BaseModel):
    labels: dict[str, str]
    uuid: str


class V1Alpha1ExporterStatus(BaseModel):
    credential: SerializeV1ObjectReference
    devices: list[V1Alpha1ExporterDevice]
    endpoint: str

    model_config = ConfigDict(arbitrary_types_allowed=True)


class V1Alpha1Exporter(BaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Exporter"] = Field(default="Exporter")
    metadata: SerializeV1ObjectMeta
    status: V1Alpha1ExporterStatus

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(by_alias=True), indent=2)


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

    async def list_exporters(self) -> V1Alpha1List[V1Alpha1Exporter]:
        """List the exporter objects in the cluster"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1"
        )
        return V1Alpha1List(items=[ExportersV1Alpha1Api._deserialize(c) for c in res["items"]])

    async def get_exporter(self, name: str) -> V1Alpha1Exporter:
        """Get a single exporter object from the cluster"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1", name=name
        )
        return ExportersV1Alpha1Api._deserialize(result)

    async def create_exporter(
        self, name: str, labels: dict[str, str] | None = None, oidc_username: str | None = None
    ) -> V1Alpha1Exporter:
        """Create an exporter in the cluster"""
        # Create the namespaced exporter object
        await self.api.create_namespaced_custom_object(
            namespace=self.namespace,
            group="jumpstarter.dev",
            plural="exporters",
            version="v1alpha1",
            body={
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Exporter",
                "metadata": {"name": name} | {"labels": labels} if labels is not None else {},
                "spec": {"username": oidc_username} if oidc_username is not None else {},
            },
        )
        # Wait for the credentials to become available
        # NOTE: Watch is not working here with the Python kubernetes library
        count = 0
        updated_exporter = {}
        # Retry for a maximum of 10s
        while count < CREATE_EXPORTER_COUNT:
            # Try to get the updated client resource
            updated_exporter = await self.api.get_namespaced_custom_object(
                namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1", name=name
            )
            # check if the client status is updated with the credentials
            if "status" in updated_exporter:
                if "credential" in updated_exporter["status"]:
                    return ExportersV1Alpha1Api._deserialize(updated_exporter)
            count += 1
            await asyncio.sleep(CREATE_EXPORTER_DELAY)
        raise Exception("Timeout waiting for exporter credentials")

    async def get_exporter_config(self, name: str) -> ExporterConfigV1Alpha1:
        """Get an exporter config for a specified exporter name"""
        exporter = await self.get_exporter(name)
        secret = await self.core_api.read_namespaced_secret(exporter.status.credential.name, self.namespace)
        endpoint = exporter.status.endpoint
        token = base64.b64decode(secret.data["token"]).decode("utf8")
        return ExporterConfigV1Alpha1(
            alias=name,
            metadata=ObjectMeta(
                namespace=exporter.metadata.namespace,
                name=exporter.metadata.name,
            ),
            endpoint=endpoint,
            token=token,
            export={},
        )

    async def delete_exporter(self, name: str):
        """Delete an exporter object"""
        await self.api.delete_namespaced_custom_object(
            namespace=self.namespace,
            name=name,
            group="jumpstarter.dev",
            plural="exporters",
            version="v1alpha1",
        )

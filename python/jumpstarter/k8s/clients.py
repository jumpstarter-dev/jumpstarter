import pprint
import time
from typing import Optional

from kubernetes import client, watch


class ClientsV1Alpha1Api:
    """Interact with the clients custom resource API"""
    api: client.CustomObjectsApi

    def __init__(self):
        self.api = client.CustomObjectsApi()

    def create_namespaced_client(self, namespace: str, name: str):
        """Create a client object in the cluster"""
        self.api.create_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            body={
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Client",
                "metadata": {
                    "name": name
                }
            }
        )
        # time.sleep(10)
        # client = self.get_namespaced_client(namespace, name)
        # pprint.pp(client)
        stream = self.api.list_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            field_selector=f"metadata.name={name}",
            watch=True
        )
        for event in stream:
            pprint.pp(event)

    def get_namespaced_clients(self, namespace: str):
        """List the client objects in the cluster"""
        res = self.api.list_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1"
        )
        return res["items"]

    def get_namespaced_client(self, namespace: str, name: str) -> Optional[object]:
        """Get a single client object from the cluster"""
        res = self.api.get_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            name=name
        )
        return res

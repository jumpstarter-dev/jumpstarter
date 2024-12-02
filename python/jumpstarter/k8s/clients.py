from typing import Optional

from kubernetes import client


class ClientsV1Alpha1Api:
    """Interact with the clients custom resource API"""
    api: client.CustomObjectsApi

    def __init__(self):
        self.api = client.CustomObjectsApi()

    def get_namespaced_clients(self, namespace: str):
        """List the client objects in the cluster."""
        res = self.api.list_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1"
        )
        return res["items"]

    def get_namespaced_client(self, namespace: str, name: str) -> Optional[object]:
        """Get a single client object from the cluster."""
        res = self.api.get_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="clients",
            version="v1alpha1",
            name=name
        )
        return res

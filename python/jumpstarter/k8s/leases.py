from typing import Optional

from kubernetes import client


class LeasesV1Alpha1Api:
    """Interact with the leases custom resource API"""
    api: client.CustomObjectsApi

    def __init__(self):
        self.api = client.CustomObjectsApi()

    def get_namespaced_leases(self, namespace: str):
        """List the lease objects in the cluster."""
        res = self.api.list_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="leases",
            version="v1alpha1"
        )
        return res["items"]

    def get_namespaced_lease(self, namespace: str, name: str) -> Optional[object]:
        """Get a single lease object from the cluster."""
        res = self.api.get_namespaced_custom_object(
            namespace=namespace,
            group="jumpstarter.dev",
            plural="leases",
            version="v1alpha1",
            name=name
        )
        return res

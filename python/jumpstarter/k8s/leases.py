from typing import Optional

from .util import AbstractAsyncCustomObjectApi


class LeasesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the leases custom resource API"""

    async def list_leases(self):
        """List the lease objects in the cluster async"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1"
        )
        return res["items"]

    async def get_lease(self, name: str) -> Optional[object]:
        """Get a single lease object from the cluster async"""
        res = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1", name=name
        )
        return res

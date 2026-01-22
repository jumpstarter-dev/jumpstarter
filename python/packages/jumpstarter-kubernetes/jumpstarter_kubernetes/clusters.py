from typing import Literal, Optional

from pydantic import Field

from .json import JsonBaseModel
from .list import V1Alpha1List


class V1Alpha1JumpstarterInstance(JsonBaseModel):
    """Information about Jumpstarter installation in a cluster"""

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["JumpstarterInstance"] = Field(default="JumpstarterInstance")
    installed: bool
    version: Optional[str] = None
    namespace: Optional[str] = None
    chart_name: Optional[str] = Field(alias="chartName", default=None)
    status: Optional[str] = None
    has_crds: bool = Field(alias="hasCrds", default=False)
    error: Optional[str] = None
    basedomain: Optional[str] = None
    controller_endpoint: Optional[str] = Field(alias="controllerEndpoint", default=None)
    router_endpoint: Optional[str] = Field(alias="routerEndpoint", default=None)


class V1Alpha1ClusterInfo(JsonBaseModel):
    """Information about a Kubernetes cluster"""

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["ClusterInfo"] = Field(default="ClusterInfo")
    name: str
    cluster: str
    server: str
    user: str
    namespace: str
    is_current: bool = Field(alias="isCurrent")
    type: Literal["kind", "minikube", "remote"]
    accessible: bool
    version: Optional[str] = None
    jumpstarter: V1Alpha1JumpstarterInstance
    error: Optional[str] = None

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        table.add_column("CURRENT")
        table.add_column("NAME")
        table.add_column("TYPE")
        table.add_column("STATUS")
        table.add_column("JUMPSTARTER")
        table.add_column("VERSION")
        table.add_column("NAMESPACE")


    def rich_add_rows(self, table, **kwargs):
        # Current indicator
        current = "*" if self.is_current else ""

        # Status
        status = "Running" if self.accessible else "Stopped"

        # Jumpstarter status
        jumpstarter = "Yes" if self.jumpstarter.installed else "No"
        if self.jumpstarter.error:
            jumpstarter = "Error"

        # Version and namespace
        version = self.jumpstarter.version or "-"
        namespace = self.jumpstarter.namespace or "-"

        # Base row data
        row_data = [current, self.name, self.type, status, jumpstarter, version, namespace]

        table.add_row(*row_data)

    def rich_add_names(self, names):
        names.append(f"cluster/{self.name}")


class V1Alpha1ClusterList(V1Alpha1List[V1Alpha1ClusterInfo]):
    """List of clusters"""

    kind: Literal["ClusterList"] = Field(default="ClusterList")

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1ClusterInfo.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for cluster in self.items:
            cluster.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for cluster in self.items:
            cluster.rich_add_names(names)

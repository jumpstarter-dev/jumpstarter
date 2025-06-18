from typing import Literal, Optional

from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference
from pydantic import Field

from .datetime import time_since
from .json import JsonBaseModel
from .list import V1Alpha1List
from .serialize import SerializeV1Condition, SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi


class V1Alpha1LeaseStatus(JsonBaseModel):
    begin_time: Optional[str] = Field(alias="beginTime")
    conditions: list[SerializeV1Condition]
    end_time: Optional[str] = Field(alias="endTime")
    ended: bool
    exporter: Optional[SerializeV1ObjectReference]


class V1Alpha1LeaseSelector(JsonBaseModel):
    match_labels: dict[str, str] = Field(alias="matchLabels")


class V1Alpha1LeaseSpec(JsonBaseModel):
    client: SerializeV1ObjectReference
    duration: Optional[str]
    selector: V1Alpha1LeaseSelector


class V1Alpha1Lease(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Lease"] = Field(default="Lease")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1LeaseSpec
    status: V1Alpha1LeaseStatus

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1Lease(
            api_version=dict["apiVersion"],
            kind=dict["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=dict["metadata"]["creationTimestamp"],
                generation=dict["metadata"]["generation"],
                managed_fields=dict["metadata"]["managedFields"],
                name=dict["metadata"]["name"],
                namespace=dict["metadata"]["namespace"],
                resource_version=dict["metadata"]["resourceVersion"],
                uid=dict["metadata"]["uid"],
            ),
            status=V1Alpha1LeaseStatus(
                begin_time=dict["status"]["beginTime"] if "beginTime" in dict["status"] else None,
                end_time=dict["status"]["endTime"] if "endTime" in dict["status"] else None,
                ended=dict["status"]["ended"],
                exporter=V1ObjectReference(name=dict["status"]["exporterRef"]["name"])
                if "exporterRef" in dict["status"]
                else None,
                conditions=[
                    V1Condition(
                        last_transition_time=cond["lastTransitionTime"],
                        message=cond["message"],
                        observed_generation=cond["observedGeneration"],
                        reason=cond["reason"],
                        status=cond["status"],
                        type=cond["type"],
                    )
                    for cond in dict["status"]["conditions"]
                ],
            ),
            spec=V1Alpha1LeaseSpec(
                client=V1ObjectReference(name=dict["spec"]["clientRef"]["name"])
                if "clientRef" in dict["spec"]
                else None,
                duration=dict["spec"]["duration"] if "duration" in dict["spec"] else None,
                selector=V1Alpha1LeaseSelector(match_labels=dict["spec"]["selector"]["matchLabels"]),
            ),
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME")
        table.add_column("CLIENT")
        table.add_column("SELECTOR")
        table.add_column("EXPORTER")
        table.add_column("DURATION")
        table.add_column("STATUS")
        table.add_column("REASON")
        table.add_column("BEGIN")
        table.add_column("END")
        table.add_column("AGE")

    def get_reason(self):
        condition = self.status.conditions[-1] if len(self.status.conditions) > 0 else None
        reason = condition.reason if condition is not None else "Unknown"
        status = condition.status if condition is not None else "False"
        if reason == "Ready":
            if status == "True":
                return "Ready"
            else:
                return "Waiting"
        elif reason == "Expired":
            if status == "True":
                return "Expired"
            else:
                return "Complete"
        else:
            return reason

    def rich_add_rows(self, table):
        selectors = []
        for label in self.spec.selector.match_labels:
            selectors.append(f"{label}:{str(self.spec.selector.match_labels[label])}")
        table.add_row(
            self.metadata.name,
            self.spec.client.name if self.spec.client is not None else "",
            ",".join(selectors) if len(selectors) > 0 else "*",
            self.status.exporter.name if self.status.exporter is not None else "",
            self.spec.duration,
            "Ended" if self.status.ended else "InProgress",
            self.get_reason(),
            self.status.begin_time if self.status.begin_time is not None else "",
            self.status.end_time if self.status.end_time is not None else "",
            time_since(self.metadata.creation_timestamp),
        )

    def rich_add_names(self, names):
        names.append(f"lease.jumpstarter.dev/{self.metadata.name}")


class V1Alpha1LeaseList(V1Alpha1List[V1Alpha1Lease]):
    kind: Literal["LeaseList"] = Field(default="LeaseList")

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1LeaseList(items=[V1Alpha1Lease.from_dict(c) for c in dict["items"]])

    @classmethod
    def rich_add_columns(cls, table):
        V1Alpha1Lease.rich_add_columns(table)

    def rich_add_rows(self, table):
        for lease in self.items:
            lease.rich_add_rows(table)

    def rich_add_names(self, names):
        for lease in self.items:
            lease.rich_add_names(names)


class LeasesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the leases custom resource API"""

    async def list_leases(self) -> V1Alpha1List[V1Alpha1Lease]:
        """List the lease objects in the cluster async"""
        result = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1"
        )
        return V1Alpha1LeaseList.from_dict(result)

    async def get_lease(self, name: str) -> V1Alpha1Lease:
        """Get a single lease object from the cluster async"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group="jumpstarter.dev", plural="leases", version="v1alpha1", name=name
        )
        return V1Alpha1Lease.from_dict(result)

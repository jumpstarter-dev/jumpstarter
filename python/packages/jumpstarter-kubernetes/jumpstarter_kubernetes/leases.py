from collections.abc import Mapping
from typing import Literal

from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference
from pydantic import Field

from .datetime import time_since
from .json import JsonBaseModel, OmitNoneModel
from .list import V1Alpha1List
from .serialize import SerializeV1Condition, SerializeV1ObjectMeta, SerializeV1ObjectReference
from .util import AbstractAsyncCustomObjectApi


class V1Alpha1LocalObjectReference(JsonBaseModel):
    name: str


class V1Alpha1LabelSelector(OmitNoneModel):
    """A Kubernetes label selector, as a lease member or a template writes it."""

    match_labels: dict[str, str] | None = Field(alias="matchLabels", default=None)
    match_expressions: list[dict] | None = Field(alias="matchExpressions", default=None)

    def summary(self) -> str:
        parts = [f"{key}:{value!s}" for key, value in (self.match_labels or {}).items()]
        for expression in self.match_expressions or []:
            values = ",".join(expression.get("values") or [])
            parts.append(f"{expression.get('key', '')} {expression.get('operator', '')} {values}".strip())
        return ",".join(parts)


class V1Alpha1LeaseMember(OmitNoneModel):
    """One role of a member-form lease or of a lease template."""

    name: str
    selector: V1Alpha1LabelSelector | None = None
    exporter_ref: V1Alpha1LocalObjectReference | None = Field(alias="exporterRef", default=None)
    optional: bool | None = None
    excluded: bool | None = None
    allow_disabled: bool | None = Field(alias="allowDisabled", default=None)

    def target(self) -> str:
        """What the member selects: an exporter name or its label selector."""
        if self.exporter_ref is not None:
            return self.exporter_ref.name
        return self.selector.summary() if self.selector is not None else ""


class V1Alpha1LeaseConstraint(JsonBaseModel):
    members: list[str]
    same_label: str = Field(alias="sameLabel")


class V1Alpha1LeaseTemplateReference(JsonBaseModel):
    name: str
    generation: int


class V1Alpha1LeaseMemberStatus(OmitNoneModel):
    name: str
    state: str
    reason: str | None = None
    message: str | None = None
    exporter_ref: V1Alpha1LocalObjectReference | None = Field(alias="exporterRef", default=None)
    priority: int | None = None
    spot_access: bool | None = Field(alias="spotAccess", default=None)


class V1Alpha1LeaseStatus(OmitNoneModel):
    # A scalar lease prints as it did before member-form leases existed.
    omit_none_fields = frozenset({"members", "exclusion_groups", "bind_attempt"})

    begin_time: str | None = Field(alias="beginTime")
    conditions: list[SerializeV1Condition]
    end_time: str | None = Field(alias="endTime")
    ended: bool
    exporter: SerializeV1ObjectReference | None
    members: list[V1Alpha1LeaseMemberStatus] | None = None
    exclusion_groups: list[str] | None = Field(alias="exclusionGroups", default=None)
    bind_attempt: int | None = Field(alias="bindAttempt", default=None)


class V1Alpha1LeaseSelector(JsonBaseModel):
    match_labels: dict[str, str] = Field(alias="matchLabels")


class V1Alpha1LeaseSpec(OmitNoneModel):
    omit_none_fields = frozenset({"members", "constraints", "template_ref"})

    client: SerializeV1ObjectReference
    duration: str | None
    selector: V1Alpha1LeaseSelector
    members: list[V1Alpha1LeaseMember] | None = None
    constraints: list[V1Alpha1LeaseConstraint] | None = None
    template_ref: V1Alpha1LeaseTemplateReference | None = Field(alias="templateRef", default=None)


class V1Alpha1Lease(JsonBaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["Lease"] = Field(default="Lease")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1LeaseSpec
    status: V1Alpha1LeaseStatus

    @staticmethod
    def from_dict(data: dict):
        spec = data["spec"]
        if not isinstance(spec, Mapping):
            raise TypeError(f"spec must be a dict, got {type(spec).__name__}: {spec!r}")
        selector_data = spec.get("selector", {})
        status = data["status"]
        return V1Alpha1Lease(
            apiVersion=data["apiVersion"],
            kind=data["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=data["metadata"]["creationTimestamp"],
                generation=data["metadata"]["generation"],
                managed_fields=data["metadata"]["managedFields"],
                name=data["metadata"]["name"],
                namespace=data["metadata"]["namespace"],
                resource_version=data["metadata"]["resourceVersion"],
                uid=data["metadata"]["uid"],
            ),
            status=V1Alpha1LeaseStatus(
                beginTime=data["status"].get("beginTime", None),
                endTime=data["status"].get("endTime", None),
                ended=data["status"]["ended"],
                exporter=V1ObjectReference(name=data["status"]["exporterRef"]["name"])
                if "exporterRef" in data["status"]
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
                    # A lease the controller has not reconciled yet has no conditions.
                    for cond in status.get("conditions", [])
                ],
                members=[V1Alpha1LeaseMemberStatus.model_validate(m) for m in status["members"]]
                if "members" in status
                else None,
                exclusionGroups=status.get("exclusionGroups"),
                bindAttempt=status.get("bindAttempt"),
            ),
            spec=V1Alpha1LeaseSpec(
                client=V1ObjectReference(name=spec["clientRef"]["name"]) if "clientRef" in spec else None,
                duration=spec.get("duration", None),
                selector=V1Alpha1LeaseSelector(matchLabels=selector_data.get("matchLabels", {})),
                members=[V1Alpha1LeaseMember.model_validate(m) for m in spec["members"]] if "members" in spec else None,
                constraints=[V1Alpha1LeaseConstraint.model_validate(c) for c in spec["constraints"]]
                if "constraints" in spec
                else None,
                templateRef=V1Alpha1LeaseTemplateReference.model_validate(spec["templateRef"])
                if "templateRef" in spec
                else None,
            ),
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
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

    def is_member_form(self) -> bool:
        return bool(self.spec.members)

    def members_summary(self) -> str:
        """What a member-form lease asks for: its template, or each role's selection."""
        if self.spec.template_ref is not None:
            return f"template={self.spec.template_ref.name}"
        return " ".join(f"{member.name}={member.target()}" for member in self.spec.members or [])

    def member_exporters_summary(self) -> str:
        """The exporter of each role, with the state of every role that is not bound."""
        statuses = {member.name: member for member in self.status.members or []}
        parts = []
        for member in self.spec.members or []:
            status = statuses.get(member.name)
            # A lease the controller has not reconciled yet has no member status.
            state = status.state if status is not None else "Pending"
            exporter = status.exporter_ref.name if status is not None and status.exporter_ref is not None else ""
            if state == "Bound":
                parts.append(f"{member.name}={exporter}")
            else:
                parts.append(f"{member.name}={exporter}({state})")
        return " ".join(parts)

    def rich_add_rows(self, table):
        selectors = []
        for label in self.spec.selector.match_labels:
            selectors.append(f"{label}:{self.spec.selector.match_labels[label]!s}")
        if self.is_member_form():
            selector = self.members_summary()
            exporter = self.member_exporters_summary()
        else:
            selector = ",".join(selectors) if len(selectors) > 0 else "*"
            exporter = self.status.exporter.name if self.status.exporter is not None else ""
        table.add_row(
            self.metadata.name,
            self.spec.client.name if self.spec.client is not None else "",
            selector,
            exporter,
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
    kind: Literal["LeaseList"] = Field(default="LeaseList", frozen=True)

    @staticmethod
    def from_dict(data: dict):
        return V1Alpha1LeaseList(items=[V1Alpha1Lease.from_dict(c) for c in data["items"]])

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

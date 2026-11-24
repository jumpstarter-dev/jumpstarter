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


class V1Alpha1ForwardEndpoint(JsonBaseModel):
    """One end of a forward: a member of the lease and a port of its exporter."""

    member: str
    port: str


class V1Alpha1LeaseForward(JsonBaseModel):
    """A forward of a lease or of a lease template: it connects ports on different members."""

    name: str
    endpoints: list[V1Alpha1ForwardEndpoint]

    def summary(self) -> str:
        """``name(member.port,member.port)``"""
        return f"{self.name}({','.join(f'{e.member}.{e.port}' for e in self.endpoints)})"


class V1Alpha1LeaseForwardEndStatus(OmitNoneModel):
    """The state of one end of a forward: the last report of the exporter of the member."""

    member: str
    # Pending, Ready, Connected or Failed
    state: str
    message: str | None = None
    # The sequence number of the last report that the controller accepted.
    sequence: int | None = None
    # The time of the last setup instruction for this end.
    setup_time: str | None = Field(alias="setupTime", default=None)
    # The time of the last report that the controller accepted.
    report_time: str | None = Field(alias="reportTime", default=None)


class V1Alpha1LeaseForwardStatus(OmitNoneModel):
    name: str
    # Pending, Disabled, Listening, Connected or Failed
    state: str
    message: str | None = None
    # The state of each bound end of the forward.
    ends: list[V1Alpha1LeaseForwardEndStatus] | None = None


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
    omit_none_fields = frozenset({"members", "exclusion_groups", "bind_attempt", "forwards"})

    begin_time: str | None = Field(alias="beginTime")
    conditions: list[SerializeV1Condition]
    end_time: str | None = Field(alias="endTime")
    ended: bool
    exporter: SerializeV1ObjectReference | None
    members: list[V1Alpha1LeaseMemberStatus] | None = None
    exclusion_groups: list[str] | None = Field(alias="exclusionGroups", default=None)
    bind_attempt: int | None = Field(alias="bindAttempt", default=None)
    # The state of each forward of the lease, in the order of the spec.
    forwards: list[V1Alpha1LeaseForwardStatus] | None = None


class V1Alpha1LeaseSelector(JsonBaseModel):
    match_labels: dict[str, str] = Field(alias="matchLabels")


class V1Alpha1LeaseSpec(OmitNoneModel):
    omit_none_fields = frozenset({"members", "constraints", "template_ref", "forwards"})

    client: SerializeV1ObjectReference
    duration: str | None
    selector: V1Alpha1LeaseSelector
    members: list[V1Alpha1LeaseMember] | None = None
    constraints: list[V1Alpha1LeaseConstraint] | None = None
    template_ref: V1Alpha1LeaseTemplateReference | None = Field(alias="templateRef", default=None)
    # What connects the members of the lease. A scalar lease has none.
    forwards: list[V1Alpha1LeaseForward] | None = None


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
                forwards=[V1Alpha1LeaseForwardStatus.model_validate(f) for f in status["forwards"]]
                if "forwards" in status
                else None,
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
                forwards=[V1Alpha1LeaseForward.model_validate(f) for f in spec["forwards"]]
                if "forwards" in spec
                else None,
            ),
        )

    @classmethod
    def rich_add_columns(cls, table, forwards: bool = False):
        table.add_column("NAME", no_wrap=True)
        table.add_column("CLIENT")
        table.add_column("SELECTOR")
        table.add_column("EXPORTER")
        # The FORWARDS column appears only where a lease has forwards.
        if forwards:
            table.add_column("FORWARDS")
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

    def has_forwards(self) -> bool:
        return bool(self.spec.forwards)

    def forwards_summary(self) -> str:
        """The state of each forward. Pending for a forward that the controller did not report yet."""
        states = {forward.name: forward.state for forward in self.status.forwards or []}
        return " ".join(f"{forward.name}={states.get(forward.name, 'Pending')}" for forward in self.spec.forwards or [])

    def rich_add_rows(self, table, forwards: bool = False):
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
            *([self.forwards_summary()] if forwards else []),
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

    def has_forwards(self) -> bool:
        return any(lease.has_forwards() for lease in self.items)

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1Lease.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for lease in self.items:
            lease.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for lease in self.items:
            lease.rich_add_names(names)


class LeasesV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the leases custom resource API"""

    async def list_leases(self) -> V1Alpha1LeaseList:
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

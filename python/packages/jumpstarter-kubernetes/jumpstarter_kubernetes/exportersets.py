from typing import Literal

from kubernetes_asyncio.client.models import V1ObjectMeta
from pydantic import Field

from .json import JsonBaseModel
from .list import V1Alpha1List
from .serialize import SerializeV1ObjectMeta
from .util import AbstractAsyncCustomObjectApi

# ExporterSet lives in its own API group, not the core jumpstarter.dev one.
EXPORTER_SET_GROUP = "virtualtarget.jumpstarter.dev"


class V1Alpha1ExporterSetMember(JsonBaseModel):
    """One exporter owned by a set, as a client would see it by name/labels."""

    name: str
    labels: dict[str, str] = Field(default_factory=dict)


class V1Alpha1ExporterSetSpec(JsonBaseModel):
    """The parts of an ExporterSet spec a consumer needs to lease from it.

    `selector` is what identifies the pool: a lease carrying these labels is
    served by this set, and the controller provisions an exporter for it when
    none is free — so a client can lease from a set that currently has no
    instances at all.
    """

    # Labels stamped on the exporters this set creates. A lease is served by
    # this set when its selector is satisfied by these labels, so this is what
    # a client selects on to lease from the pool.
    template_labels: dict[str, str] = Field(alias="templateLabels", default_factory=dict)
    # Labels the set uses to find the exporters it owns.
    selector: dict[str, str] = Field(default_factory=dict)
    virtual_target_class: str | None = Field(alias="virtualTargetClassName", default=None)
    # Resolved from the referenced VirtualTargetClass (e.g. qemu.jumpstarter.dev).
    provisioner: str | None = None
    binding_mode: str | None = Field(alias="bindingMode", default=None)
    min_replicas: int | None = Field(alias="minReplicas", default=None)
    max_replicas: int | None = Field(alias="maxReplicas", default=None)
    # The warm buffer: exporters kept ready and unleased ahead of demand.
    min_available_replicas: int | None = Field(alias="minAvailableReplicas", default=None)
    scale_down_cooldown: str | None = Field(alias="scaleDownCooldown", default=None)
    recycle_strategy: str | None = Field(alias="recycleStrategy", default=None)


class V1Alpha1ExporterSetStatus(JsonBaseModel):
    replicas: int | None = None
    ready_replicas: int | None = Field(alias="readyReplicas", default=None)
    available_replicas: int | None = Field(alias="availableReplicas", default=None)
    unavailable_replicas: int | None = Field(alias="unavailableReplicas", default=None)
    leased_replicas: int | None = Field(alias="leasedReplicas", default=None)
    pods_pending: int | None = Field(alias="podsPending", default=None)
    pods_running: int | None = Field(alias="podsRunning", default=None)
    pods_failed: int | None = Field(alias="podsFailed", default=None)
    exporters_active: int | None = Field(alias="exportersActive", default=None)
    exporters_idle: int | None = Field(alias="exportersIdle", default=None)
    exporters_offline: int | None = Field(alias="exportersOffline", default=None)


class V1Alpha1ExporterSet(JsonBaseModel):
    api_version: Literal["virtualtarget.jumpstarter.dev/v1alpha1"] = Field(
        alias="apiVersion", default="virtualtarget.jumpstarter.dev/v1alpha1"
    )
    kind: Literal["ExporterSet"] = Field(default="ExporterSet")
    metadata: SerializeV1ObjectMeta
    spec: V1Alpha1ExporterSetSpec = Field(default_factory=V1Alpha1ExporterSetSpec)
    status: V1Alpha1ExporterSetStatus = Field(default_factory=V1Alpha1ExporterSetStatus)
    # Exporters this set currently owns; empty for a pool scaled to zero.
    exporters: list[V1Alpha1ExporterSetMember] = Field(default_factory=list)

    @staticmethod
    def from_dict(dict: dict):
        spec = dict.get("spec") or {}
        status = dict.get("status") or {}
        metadata = dict.get("metadata") or {}
        return V1Alpha1ExporterSet(
            api_version=dict["apiVersion"],
            kind=dict["kind"],
            metadata=V1ObjectMeta(
                creation_timestamp=metadata.get("creationTimestamp"),
                generation=metadata.get("generation"),
                name=metadata.get("name"),
                namespace=metadata.get("namespace"),
                resource_version=metadata.get("resourceVersion"),
                uid=metadata.get("uid"),
            ),
            spec=V1Alpha1ExporterSetSpec(
                selector=(spec.get("selector") or {}).get("matchLabels") or {},
                templateLabels=((spec.get("template") or {}).get("metadata") or {}).get("labels") or {},
                virtualTargetClassName=spec.get("virtualTargetClassName"),
                minReplicas=spec.get("minReplicas"),
                maxReplicas=spec.get("maxReplicas"),
                minAvailableReplicas=spec.get("minAvailableReplicas"),
                scaleDownCooldown=spec.get("scaleDownCooldown"),
                recycleStrategy=spec.get("recycleStrategy"),
            ),
            status=V1Alpha1ExporterSetStatus(
                replicas=status.get("replicas"),
                readyReplicas=status.get("readyReplicas"),
                availableReplicas=status.get("availableReplicas"),
                unavailableReplicas=status.get("unavailableReplicas"),
                leasedReplicas=status.get("leasedReplicas"),
                podsPending=status.get("podsPending"),
                podsRunning=status.get("podsRunning"),
                podsFailed=status.get("podsFailed"),
                exportersActive=status.get("exportersActive"),
                exportersIdle=status.get("exportersIdle"),
                exportersOffline=status.get("exportersOffline"),
            ),
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
        table.add_column("PROVISIONER")
        table.add_column("SELECTOR")
        table.add_column("REPLICAS")
        table.add_column("READY")
        table.add_column("LEASED")
        table.add_column("WARM")

    def rich_add_rows(self, table):
        def count(value: int | None) -> str:
            return "-" if value is None else str(value)

        replicas = count(self.status.replicas)
        if self.spec.max_replicas:
            replicas = f"{replicas}/{self.spec.max_replicas}"

        table.add_row(
            self.metadata.name,
            self.spec.provisioner or self.spec.virtual_target_class or "-",
            ",".join(f"{k}={v}" for k, v in sorted(self.spec.template_labels.items())),
            replicas,
            count(self.status.ready_replicas),
            count(self.status.leased_replicas),
            count(self.spec.min_available_replicas),
        )

    def rich_add_names(self, names):
        names.append(self.metadata.name)


class V1Alpha1ExporterSetList(V1Alpha1List[V1Alpha1ExporterSet]):
    kind: Literal["ExporterSetList"] = Field(default="ExporterSetList")

    @staticmethod
    def from_dict(dict: dict):
        return V1Alpha1ExporterSetList(
            items=[V1Alpha1ExporterSet.from_dict(item) for item in dict["items"]],
        )

    @classmethod
    def rich_add_columns(cls, table, **kwargs):
        V1Alpha1ExporterSet.rich_add_columns(table, **kwargs)

    def rich_add_rows(self, table, **kwargs):
        for exporter_set in self.items:
            exporter_set.rich_add_rows(table, **kwargs)

    def rich_add_names(self, names):
        for exporter_set in self.items:
            exporter_set.rich_add_names(names)


class ExporterSetsV1Alpha1Api(AbstractAsyncCustomObjectApi):
    """Interact with the exportersets custom resource API"""

    async def _class_provisioners(self) -> dict[str, tuple[str | None, str | None]]:
        """Map VirtualTargetClass name -> (provisioner, binding mode).

        The provisioner is a property of the class an exporter set references,
        not of the set itself, so it has to be resolved to be reportable.
        """
        try:
            res = await self.api.list_namespaced_custom_object(
                namespace=self.namespace,
                group=EXPORTER_SET_GROUP,
                plural="virtualtargetclasses",
                version="v1alpha1",
            )
        except Exception:
            # Reading classes is a nicety; never fail the listing over it.
            return {}
        classes = {}
        for item in res.get("items", []):
            spec = item.get("spec") or {}
            classes[(item.get("metadata") or {}).get("name")] = (
                spec.get("provisioner"),
                spec.get("bindingMode"),
            )
        return classes

    async def _owned_exporters(self) -> dict[str, list[V1Alpha1ExporterSetMember]]:
        """Map exporter set UID -> the exporters it owns.

        Membership lives only in each exporter's ownerReferences; the set's
        status carries counts, not names.
        """
        try:
            res = await self.api.list_namespaced_custom_object(
                namespace=self.namespace, group="jumpstarter.dev", plural="exporters", version="v1alpha1"
            )
        except Exception:
            return {}
        owned: dict[str, list[V1Alpha1ExporterSetMember]] = {}
        for item in res.get("items", []):
            metadata = item.get("metadata") or {}
            for owner in metadata.get("ownerReferences") or []:
                if owner.get("kind") != "ExporterSet":
                    continue
                owned.setdefault(owner.get("uid"), []).append(
                    V1Alpha1ExporterSetMember(
                        name=metadata.get("name"),
                        labels=metadata.get("labels") or {},
                    )
                )
        for members in owned.values():
            members.sort(key=lambda member: member.name)
        return owned

    async def _enrich(self, exporter_set: V1Alpha1ExporterSet) -> V1Alpha1ExporterSet:
        classes = await self._class_provisioners()
        owned = await self._owned_exporters()
        provisioner, binding = classes.get(exporter_set.spec.virtual_target_class, (None, None))
        exporter_set.spec.provisioner = provisioner
        exporter_set.spec.binding_mode = binding
        exporter_set.exporters = owned.get(exporter_set.metadata.uid, [])
        return exporter_set

    async def list_exporter_sets(self) -> V1Alpha1ExporterSetList:
        """List the exporter set objects in the cluster, with their exporters"""
        res = await self.api.list_namespaced_custom_object(
            namespace=self.namespace, group=EXPORTER_SET_GROUP, plural="exportersets", version="v1alpha1"
        )
        result = V1Alpha1ExporterSetList.from_dict(res)
        classes = await self._class_provisioners()
        owned = await self._owned_exporters()
        for exporter_set in result.items:
            provisioner, binding = classes.get(exporter_set.spec.virtual_target_class, (None, None))
            exporter_set.spec.provisioner = provisioner
            exporter_set.spec.binding_mode = binding
            exporter_set.exporters = owned.get(exporter_set.metadata.uid, [])
        return result

    async def get_exporter_set(self, name: str) -> V1Alpha1ExporterSet:
        """Get a single exporter set object from the cluster, with its exporters"""
        result = await self.api.get_namespaced_custom_object(
            namespace=self.namespace, group=EXPORTER_SET_GROUP, plural="exportersets", version="v1alpha1", name=name
        )
        return await self._enrich(V1Alpha1ExporterSet.from_dict(result))

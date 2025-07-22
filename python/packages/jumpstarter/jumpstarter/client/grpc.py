from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from google.protobuf import duration_pb2, field_mask_pb2, json_format
from grpc import ChannelConnectivity
from grpc.aio import Channel
from jumpstarter_protocol import client_pb2, client_pb2_grpc, jumpstarter_pb2_grpc, kubernetes_pb2, router_pb2_grpc
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from jumpstarter.common.grpc import translate_grpc_exceptions


def parse_identifier(identifier: str, kind: str) -> tuple[str, str]:
    segments = identifier.split("/")
    if len(segments) != 4:
        raise ValueError("incorrect number of segments in identifier, expecting 4, got {}".format(len(segments)))
    if segments[0] != "namespaces":
        raise ValueError("incorrect first segment in identifier, expecting namespaces, got {}".format(segments[0]))
    if segments[2] != kind:
        raise ValueError("incorrect third segment in identifier, expecting {}, got {}".format(kind, segments[2]))
    return segments[1], segments[3]


def parse_client_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "clients")


def parse_exporter_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "exporters")


def parse_lease_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "leases")


class Exporter(BaseModel):
    namespace: str
    name: str
    labels: dict[str, str]

    @classmethod
    def from_protobuf(cls, data: client_pb2.Exporter) -> Exporter:
        namespace, name = parse_exporter_identifier(data.name)
        return cls(namespace=namespace, name=name, labels=data.labels)

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME")
        table.add_column("LABELS")

    def rich_add_rows(self, table):
        table.add_row(
            self.name,
            ",".join(("{}={}".format(i[0], i[1]) for i in self.labels.items())),
        )

    def rich_add_names(self, names):
        names.append(self.name)


class Lease(BaseModel):
    namespace: str
    name: str
    selector: str
    duration: timedelta
    client: str
    exporter: str
    conditions: list[kubernetes_pb2.Condition]
    effective_begin_time: datetime | None = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ser_json_timedelta="float",
    )

    @field_serializer("conditions")
    def serialize_conditions(self, conditions: list[kubernetes_pb2.Condition], _info):
        return [json_format.MessageToDict(condition) for condition in conditions]

    @classmethod
    def from_protobuf(cls, data: client_pb2.Lease) -> Lease:
        namespace, name = parse_lease_identifier(data.name)

        _, client = parse_client_identifier(data.client)
        if data.exporter != "":
            _, exporter = parse_exporter_identifier(data.exporter)
        else:
            exporter = ""

        effective_begin_time = None
        if data.effective_begin_time:
            effective_begin_time = data.effective_begin_time.ToDatetime(
                tzinfo=datetime.now().astimezone().tzinfo,
            )

        return cls(
            namespace=namespace,
            name=name,
            selector=data.selector,
            duration=data.duration.ToTimedelta(),
            client=client,
            exporter=exporter,
            effective_begin_time=effective_begin_time,
            conditions=data.conditions,
        )

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME")
        table.add_column("SELECTOR")
        table.add_column("DURATION")
        table.add_column("CLIENT")
        table.add_column("EXPORTER")

    def rich_add_rows(self, table):
        table.add_row(
            self.name,
            self.selector,
            str(self.duration),
            self.client,
            self.exporter,
        )

    def rich_add_names(self, names):
        names.append(self.name)

    def get_status(self) -> str:
        """Get the lease status based on conditions"""
        if not self.conditions:
            return "Unknown"

        latest_condition = self.conditions[-1]

        if latest_condition.type == "Ready" and latest_condition.status == "True":
            return "In-Use"
        elif latest_condition.type == "Ready" and latest_condition.status == "False":
            return "Waiting"
        elif latest_condition.type == "Expired":
            return "Expired"
        else:
            return latest_condition.reason if latest_condition.reason else "Unknown"


class WithLease(BaseModel):
    exporter: Exporter
    lease: Lease | None = None

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME")
        table.add_column("LABELS")
        table.add_column("LEASED BY")
        table.add_column("LEASE STATUS")
        table.add_column("START TIME")

    def rich_add_rows(self, table):
        lease_client = ""
        lease_status = "Available"
        start_time = ""

        if self.lease and self.lease.exporter == self.exporter.name:
            lease_client = self.lease.client
            lease_status = self.lease.get_status()
            if self.lease.effective_begin_time:
                start_time = self.lease.effective_begin_time.strftime("%Y-%m-%d %H:%M:%S")

        table.add_row(
            self.exporter.name,
            ",".join(("{}={}".format(i[0], i[1]) for i in self.exporter.labels.items())),
            lease_client,
            lease_status,
            start_time,
        )

    def rich_add_names(self, names):
        self.exporter.rich_add_names(names)

    def model_dump_json(self, **kwargs):
        json_kwargs = {k: v for k, v in kwargs.items() if k in {"indent", "separators", "sort_keys", "ensure_ascii"}}
        data = {
            "exporter": self.exporter.model_dump(mode="json"),
            "lease": self.lease.model_dump(mode="json") if self.lease else None,
        }
        return json.dumps(data, **json_kwargs)

    def model_dump(self, **kwargs):
        return {
            "exporter": self.exporter.model_dump(mode="json"),
            "lease": self.lease.model_dump(mode="json") if self.lease else None,
        }


class ExporterList(BaseModel):
    exporters: list[Exporter]
    next_page_token: str | None = Field(exclude=True)

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListExportersResponse) -> ExporterList:
        return cls(
            exporters=list(map(Exporter.from_protobuf, data.exporters)),
            next_page_token=data.next_page_token,
        )

    @classmethod
    def rich_add_columns(cls, table):
        Exporter.rich_add_columns(table)

    def rich_add_rows(self, table):
        for exporter in self.exporters:
            exporter.rich_add_rows(table)

    def rich_add_names(self, names):
        for exporter in self.exporters:
            exporter.rich_add_names(names)


class WithLeaseList(BaseModel):
    """List of exporters with lease information"""

    exporters_with_leases: list[WithLease]
    next_page_token: str | None = Field(exclude=True)

    @classmethod
    def rich_add_columns(cls, table):
        WithLease.rich_add_columns(table)

    def rich_add_rows(self, table):
        for exporter_with_lease in self.exporters_with_leases:
            exporter_with_lease.rich_add_rows(table)

    def rich_add_names(self, names):
        for exporter_with_lease in self.exporters_with_leases:
            exporter_with_lease.rich_add_names(names)

    def model_dump_json(self, **kwargs):
        json_kwargs = {k: v for k, v in kwargs.items() if k in {"indent", "separators", "sort_keys", "ensure_ascii"}}
        data = {
            "exporters": [
                {
                    "exporter": ewl.exporter.model_dump(mode="json"),
                    "lease": ewl.lease.model_dump(mode="json") if ewl.lease else None,
                }
                for ewl in self.exporters_with_leases
            ]
        }
        return json.dumps(data, **json_kwargs)

    def model_dump(self, **kwargs):
        return {
            "exporters": [
                {
                    "exporter": ewl.exporter.model_dump(mode="json"),
                    "lease": ewl.lease.model_dump(mode="json") if ewl.lease else None,
                }
                for ewl in self.exporters_with_leases
            ]
        }


class LeaseList(BaseModel):
    leases: list[Lease]
    next_page_token: str | None = Field(exclude=True)

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListLeasesResponse) -> LeaseList:
        return cls(
            leases=list(map(Lease.from_protobuf, data.leases)),
            next_page_token=data.next_page_token,
        )

    @classmethod
    def rich_add_columns(cls, table):
        Lease.rich_add_columns(table)

    def rich_add_rows(self, table):
        for lease in self.leases:
            lease.rich_add_rows(table)

    def rich_add_names(self, names):
        for lease in self.leases:
            lease.rich_add_names(names)


@dataclass(kw_only=True, slots=True)
class ClientService:
    channel: Channel
    namespace: str
    stub: client_pb2_grpc.ClientServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = client_pb2_grpc.ClientServiceStub(channel=self.channel)

    async def GetExporter(self, *, name: str):
        with translate_grpc_exceptions():
            exporter = await self.stub.GetExporter(
                client_pb2.GetExporterRequest(
                    name="namespaces/{}/exporters/{}".format(self.namespace, name),
                )
            )
        return Exporter.from_protobuf(exporter)

    async def ListExporters(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
    ):
        with translate_grpc_exceptions():
            exporters = await self.stub.ListExporters(
                client_pb2.ListExportersRequest(
                    parent="namespaces/{}".format(self.namespace),
                    page_size=page_size,
                    page_token=page_token,
                    filter=filter,
                )
            )
        return ExporterList.from_protobuf(exporters)

    async def GetLease(self, *, name: str):
        with translate_grpc_exceptions():
            lease = await self.stub.GetLease(
                client_pb2.GetLeaseRequest(
                    name="namespaces/{}/leases/{}".format(self.namespace, name),
                )
            )
        return Lease.from_protobuf(lease)

    async def ListLeases(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
    ):
        with translate_grpc_exceptions():
            leases = await self.stub.ListLeases(
                client_pb2.ListLeasesRequest(
                    parent="namespaces/{}".format(self.namespace),
                    page_size=page_size,
                    page_token=page_token,
                    filter=filter,
                )
            )
        return LeaseList.from_protobuf(leases)

    async def CreateLease(
        self,
        *,
        selector: str,
        duration: timedelta,
    ):
        duration_pb = duration_pb2.Duration()
        duration_pb.FromTimedelta(duration)

        with translate_grpc_exceptions():
            lease = await self.stub.CreateLease(
                client_pb2.CreateLeaseRequest(
                    parent="namespaces/{}".format(self.namespace),
                    lease=client_pb2.Lease(
                        duration=duration_pb,
                        selector=selector,
                    ),
                )
            )
        return Lease.from_protobuf(lease)

    async def UpdateLease(
        self,
        *,
        name: str,
        duration: timedelta,
    ):
        duration_pb = duration_pb2.Duration()
        duration_pb.FromTimedelta(duration)

        update_mask = field_mask_pb2.FieldMask()
        update_mask.FromJsonString("duration")

        with translate_grpc_exceptions():
            lease = await self.stub.UpdateLease(
                client_pb2.UpdateLeaseRequest(
                    lease=client_pb2.Lease(
                        name="namespaces/{}/leases/{}".format(self.namespace, name),
                        duration=duration_pb,
                    ),
                    update_mask=update_mask,
                )
            )
        return Lease.from_protobuf(lease)

    async def DeleteLease(self, *, name: str):
        with translate_grpc_exceptions():
            await self.stub.DeleteLease(
                client_pb2.DeleteLeaseRequest(
                    name="namespaces/{}/leases/{}".format(self.namespace, name),
                )
            )


@dataclass(frozen=True, slots=True)
class MultipathExporterStub:
    """
    Multipath ExporterServiceStub

    Connecting to exporter service using multiple channels.
    All channels are tried in sequence, and the first one ready
    is used, prioritizing channels in the front.
    """

    channels: InitVar[list[Channel]]

    __stubs: dict[Channel, Any] = field(init=False, default_factory=OrderedDict)

    def __post_init__(self, channels):
        for channel in channels:
            stub = SimpleNamespace()
            jumpstarter_pb2_grpc.ExporterServiceStub.__init__(stub, channel)
            router_pb2_grpc.RouterServiceStub.__init__(stub, channel)
            self.__stubs[channel] = stub

    def __getattr__(self, name):
        for channel, stub in self.__stubs.items():
            # find the first channel that's ready
            if channel.get_state(try_to_connect=True) == ChannelConnectivity.READY:
                return getattr(stub, name)
        # or fallback to the last channel (via router)
        return getattr(next(reversed(self.__stubs.values())), name)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import yaml
from google.protobuf import duration_pb2, field_mask_pb2, json_format
from grpc.aio import Channel
from jumpstarter_protocol import client_pb2, client_pb2_grpc, kubernetes_pb2
from pydantic import BaseModel, ConfigDict, Field, field_serializer


def parse_identifier(identifier: str, kind: str) -> (str, str):
    segments = identifier.split("/")
    if len(segments) != 4:
        raise ValueError("incorrect number of segments in identifier, expecting 4, got {}".format(len(segments)))
    if segments[0] != "namespaces":
        raise ValueError("incorrect first segment in identifier, expecting namespaces, got {}".format(segments[0]))
    if segments[2] != kind:
        raise ValueError("incorrect third segment in identifier, expecting {}, got {}".format(kind, segments[2]))
    return segments[1], segments[3]


def parse_client_identifier(identifier: str) -> (str, str):
    return parse_identifier(identifier, "clients")


def parse_exporter_identifier(identifier: str) -> (str, str):
    return parse_identifier(identifier, "exporters")


def parse_lease_identifier(identifier: str) -> (str, str):
    return parse_identifier(identifier, "leases")


class Exporter(BaseModel):
    namespace: str
    name: str
    labels: dict[str, str]

    @classmethod
    def from_protobuf(cls, data: client_pb2.Exporter) -> Exporter:
        namespace, name = parse_exporter_identifier(data.name)
        return cls(namespace=namespace, name=name, labels=data.labels)


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

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)


class ExporterList(BaseModel):
    exporters: list[Exporter]
    next_page_token: str | None = Field(exclude=True)

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListExportersResponse) -> ExporterList:
        return cls(
            exporters=list(map(Exporter.from_protobuf, data.exporters)),
            next_page_token=data.next_page_token,
        )

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)


class LeaseList(BaseModel):
    leases: list[Lease]
    next_page_token: str | None = Field(exclude=True)

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListLeasesResponse) -> LeaseList:
        return cls(
            leases=list(map(Lease.from_protobuf, data.leases)),
            next_page_token=data.next_page_token,
        )

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)


@dataclass(kw_only=True, slots=True)
class ClientService:
    channel: Channel
    namespace: str
    stub: client_pb2_grpc.ClientServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = client_pb2_grpc.ClientServiceStub(channel=self.channel)

    async def GetExporter(self, *, name: str):
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
        await self.stub.DeleteLease(
            client_pb2.DeleteLeaseRequest(
                name="namespaces/{}/leases/{}".format(self.namespace, name),
            )
        )

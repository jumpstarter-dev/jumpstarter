from __future__ import annotations

from dataclasses import dataclass, field

import yaml
from grpc.aio import Channel
from jumpstarter_protocol import client_pb2, client_pb2_grpc
from pydantic import BaseModel


def parse_exporter_identifier(identifier: str) -> (str, str):
    segments = identifier.split("/")
    if len(segments) != 4:
        raise ValueError("incorrect number of segments in identifier, expecting 4, got {}".format(len(segments)))
    if segments[0] != "namespaces":
        raise ValueError("incorrect first segment in identifier, expecting namespaces, got {}".format(segments[0]))
    if segments[2] != "exporters":
        raise ValueError("incorrect third segment in identifier, expecting exporters, got {}".format(segments[2]))
    return segments[1], segments[3]


class Exporter(BaseModel):
    namespace: str
    name: str
    labels: dict[str, str]

    @classmethod
    def from_protobuf(cls, data: client_pb2.Exporter) -> Exporter:
        namespace, name = parse_exporter_identifier(data.name)
        return cls(namespace=namespace, name=name, labels=data.labels)


class ExporterList(BaseModel):
    exporters: list[Exporter]
    next_page_token: str | None

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


@dataclass(kw_only=True, slots=True)
class ClientService:
    channel: Channel
    stub: client_pb2_grpc.ClientServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = client_pb2_grpc.ClientServiceStub(channel=self.channel)

    async def GetExporter(self, *, namespace: str, name: str):
        exporter = await self.stub.GetExporter(
            client_pb2.GetExporterRequest(
                name="namespaces/{}/exporters/{}".format(namespace, name),
            )
        )
        return Exporter.from_protobuf(exporter)

    async def ListExporters(
        self,
        *,
        namespace: str,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
    ):
        exporters = await self.stub.ListExporters(
            client_pb2.ListExportersRequest(
                parent="namespaces/{}".format(namespace),
                page_size=page_size,
                page_token=page_token,
                filter=filter,
            )
        )
        return ExporterList.from_protobuf(exporters)

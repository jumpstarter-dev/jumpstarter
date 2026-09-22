"""Type aliases for gRPC and Protobuf types."""

from typing import TypeAlias

from grpc.aio import Channel
from jumpstarter_protocol import jumpstarter_pb2_grpc, router_pb2_grpc

# Stub type aliases (the generic Stub classes work for both sync and async)
ExporterStub: TypeAlias = jumpstarter_pb2_grpc.ExporterServiceStub
RouterStub: TypeAlias = router_pb2_grpc.RouterServiceStub
ControllerStub: TypeAlias = jumpstarter_pb2_grpc.ControllerServiceStub

# Channel type alias
AsyncChannel: TypeAlias = Channel

# Async stub type aliases are only available for type checking (defined in .pyi files)

__all__ = [
    "AsyncChannel",
    "ControllerStub",
    "ExporterStub",
    "RouterStub",
]

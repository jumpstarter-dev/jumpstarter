"""Type aliases for gRPC and Protobuf types."""


from grpc.aio import Channel
from jumpstarter_protocol import jumpstarter_pb2_grpc, router_pb2_grpc

# Stub type aliases (the generic Stub classes work for both sync and async)
type ExporterStub = jumpstarter_pb2_grpc.ExporterServiceStub
type RouterStub = router_pb2_grpc.RouterServiceStub
type ControllerStub = jumpstarter_pb2_grpc.ControllerServiceStub

# Channel type alias
type AsyncChannel = Channel

# Async stub type aliases are only available for type checking (defined in .pyi files)

__all__ = [
    "AsyncChannel",
    "ControllerStub",
    "ExporterStub",
    "RouterStub",
]

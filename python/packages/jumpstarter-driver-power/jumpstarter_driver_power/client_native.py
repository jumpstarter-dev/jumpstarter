"""
Native gRPC client adapter for PowerInterface.

Registers method-to-stub mappings so that DriverClient.call("on")
transparently routes through the native PowerInterface gRPC stub.
"""

from __future__ import annotations

from google.protobuf.empty_pb2 import Empty

from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.protocol.power.v1 import power_pb2_grpc
from jumpstarter.client.native import register_native_client_adapter

SERVICE_NAME = "jumpstarter.interfaces.power.v1.PowerInterface"
_UUID_METADATA_KEY = "x-jumpstarter-driver-uuid"


async def _call_on(stub, uuid, *args):
    metadata = ((_UUID_METADATA_KEY, str(uuid)),)
    await stub.On(Empty(), metadata=metadata)
    return None


async def _call_off(stub, uuid, *args):
    metadata = ((_UUID_METADATA_KEY, str(uuid)),)
    await stub.Off(Empty(), metadata=metadata)
    return None


async def _stream_read(stub, uuid, *args):
    metadata = ((_UUID_METADATA_KEY, str(uuid)),)
    async for reading in stub.Read(Empty(), metadata=metadata):
        yield PowerReading(voltage=reading.voltage, current=reading.current)


register_native_client_adapter(
    service_name=SERVICE_NAME,
    stub_class=power_pb2_grpc.PowerInterfaceStub,
    call_handlers={"on": _call_on, "off": _call_off},
    streaming_call_handlers={"read": _stream_read},
)

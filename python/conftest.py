import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import IPv4Address
from uuid import uuid4

import grpc
import pytest
from anyio import Event, create_memory_object_stream
from anyio.abc import AnyByteStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    kubernetes_pb2,
    router_pb2_grpc,
)


@dataclass(kw_only=True)
class MockRouter(router_pb2_grpc.RouterServiceServicer):
    pending: dict[str, AnyByteStream] = field(default_factory=dict)

    async def Stream(self, _request_iterator, context):
        event = Event()
        context.add_done_callback(lambda _: event.set())
        authorization = dict(list(context.invocation_metadata()))["authorization"]
        async with RouterStream(context=context) as stream:
            if authorization in self.pending:
                async with forward_stream(stream, self.pending[authorization]):
                    await event.wait()
            else:
                self.pending[authorization] = stream
                await event.wait()
                del self.pending[authorization]


@dataclass(kw_only=True)
class MockController(jumpstarter_pb2_grpc.ControllerServiceServicer):
    router_endpoint: str
    status: (
        MemoryObjectSendStream[jumpstarter_pb2.StatusResponse],
        MemoryObjectReceiveStream[jumpstarter_pb2.StatusResponse],
    ) = field(init=False, default_factory=lambda: create_memory_object_stream[jumpstarter_pb2.StatusResponse](32))
    queue: (MemoryObjectSendStream[str], MemoryObjectReceiveStream[str]) = field(
        init=False, default_factory=lambda: create_memory_object_stream[str](32)
    )

    async def Register(self, request, context):
        return jumpstarter_pb2.RegisterResponse(uuid=str(uuid4()))

    async def Unregister(self, request, context):
        return jumpstarter_pb2.UnregisterResponse()

    async def RequestLease(self, request, context):
        await self.status[0].send(jumpstarter_pb2.StatusResponse(leased=True, lease_name="dummy", client_name="dummy"))
        return jumpstarter_pb2.RequestLeaseResponse(name=str(uuid4()))

    async def GetLease(self, request, context):
        return jumpstarter_pb2.GetLeaseResponse(
            exporter_uuid=str(uuid4()),
            conditions=[
                kubernetes_pb2.Condition(
                    type="Pending",
                    status="False",
                ),
                kubernetes_pb2.Condition(
                    type="Ready",
                    status="True",
                ),
            ],
        )

    async def ReleaseLease(self, request, context):
        await self.status[0].send(jumpstarter_pb2.StatusResponse(leased=False))
        return jumpstarter_pb2.ReleaseLeaseResponse()

    async def Dial(self, request, context):
        token = str(uuid4())
        await self.queue[0].send(token)
        return jumpstarter_pb2.DialResponse(router_endpoint=self.router_endpoint, router_token=token)

    async def Status(self, request, context):
        async for status in self.status[1]:
            yield status

    async def Listen(self, request, context):
        async for token in self.queue[1]:
            yield jumpstarter_pb2.ListenResponse(router_endpoint=self.router_endpoint, router_token=token)


@pytest.fixture
def anyio_backend():
    return "asyncio"


key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())

cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([]))
    .issuer_name(x509.Name([]))
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now())
    .not_valid_after(datetime.now() + timedelta(days=365))
    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(IPv4Address("127.0.0.1"))]), critical=False)
    .sign(private_key=key, algorithm=hashes.SHA256(), backend=default_backend())
)

tls_crt = cert.public_bytes(serialization.Encoding.PEM)
tls_key = key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
)


@pytest.fixture
async def mock_controller(tmp_path, monkeypatch):
    ssl_roots = tmp_path / "ssl_roots"
    ssl_roots.write_bytes(tls_crt)

    monkeypatch.setenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", str(ssl_roots))

    server = grpc.aio.server()
    port = server.add_secure_port(
        "127.0.0.1:0", grpc.ssl_server_credentials(private_key_certificate_chain_pairs=[(tls_key, tls_crt)])
    )

    controller = MockController(router_endpoint=f"127.0.0.1:{port}")
    router = MockRouter()

    jumpstarter_pb2_grpc.add_ControllerServiceServicer_to_server(controller, server)
    router_pb2_grpc.add_RouterServiceServicer_to_server(router, server)

    await server.start()

    yield f"127.0.0.1:{port}"

    await server.stop(grace=None)


os.environ["TQDM_DISABLE"] = "1"

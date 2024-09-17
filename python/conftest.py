import os
from dataclasses import dataclass, field
from uuid import uuid4

import grpc
import pytest
from anyio import Event, create_memory_object_stream
from anyio.abc import AnyByteStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
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
    queue: (MemoryObjectSendStream[str], MemoryObjectReceiveStream[str]) = field(
        init=False, default_factory=lambda: create_memory_object_stream[str](32)
    )

    async def Register(self, request, context):
        return jumpstarter_pb2.RegisterResponse(uuid=str(uuid4()))

    async def Unregister(self, request, context):
        return jumpstarter_pb2.UnregisterResponse()

    async def RequestLease(self, request, context):
        return jumpstarter_pb2.RequestLeaseResponse(name=str(uuid4()))

    async def GetLease(self, request, context):
        return jumpstarter_pb2.GetLeaseResponse(exporter_uuid=str(uuid4()))

    async def ReleaseLease(self, request, context):
        return jumpstarter_pb2.ReleaseLeaseResponse()

    async def Dial(self, request, context):
        token = str(uuid4())
        await self.queue[0].send(token)
        return jumpstarter_pb2.DialResponse(router_endpoint=self.router_endpoint, router_token=token)

    async def Listen(self, request, context):
        async for token in self.queue[1]:
            yield jumpstarter_pb2.ListenResponse(router_endpoint=self.router_endpoint, router_token=token)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# generated with
# openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 -keyout /tmp/selfsigned.key -out /tmp/selfsigned.crt -batch

tls_key = b"""
-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCZWdZm0pa6gjVG
HJUu6m2vNefkaVL8NCDRDCUwpV2bkgyeYVKpoLBRCy7DjM3Qoh4l9ZjFSXDN3Ksp
BuFhpv1diNhBToJCAzvNOQXGHAgOtn2KPAEjgrKHpNtolL4v4knzYSIGlOpRx1oD
vZINLVtsJznYnN0lSAn+lxQ7miaop3zypQrUMaWthNriUgxlPINgJwXXdFY8Q+nP
i/Tzm0qG6BdgB3P7q3K2BsrVYEkZd/01dYQ7T//mneJneqjDxYXyId0K59XHVsZV
Ncwe7QDeefHfzh5uHFHqaWr1bZX8mxC5q8YBu2AKwTnpyYBmSkvuHXSdro4oRcL1
vgzDcdxtAgMBAAECggEANDEYQHyR4j5opUkbGRGebRB6sQmLvdx8AsoQakMN3eHS
O6FCAgt3ls2oh9OHROe4PREegp7hLp9Y/aii0pqEBu6JM4jl2lPBabJrnaZys5c2
mPKdLJnR60qXhjuBk0iABL5dV0IdkeG4aCd/6s4yHFgpXujcd1DSXfzLXRG08Jcx
Pt2IJqm6vO8BG4ixMOiwOSxz5ShixQnFxPCVKZGr7iAd3NEw61bU9b8wIzkElZAv
V/rM69StQMF2/nHgWZMFJUelhoZe2XCIPTSYhPJD/tw0AwCqc3tqmmxuLvG69RE2
lgVi9hme6n//fVhUGQ6GNvgdU9xzqCknbad1CnYcnQKBgQDU3QXpewjx3eIUw8lD
ihsvVXh7l57TJyoWaBo08NmTJPV6Ia8eXi3N89NkA8Q9qJQOyb663BJ8BcAg6xiO
OUzENhqilobLeVjT8yTZYXfKydwck4eCHCaAV9MIr2q4Ve8791WTyE4k459y6hXR
HWMygNWzayQMlPeoIMGQKKFwbwKBgQC4bWnjGtyjWFgjC1uuopJazawm6CIWzRv/
SO8LxTTFH4oxwockRLeWugzcxAe5+AO/UPV5+gbtq7IE1dx/2GvBmtOynpIUBy84
6eydSSZf7NWIo5kyGLaW6gsuTNpFnUIGvWbcgbQmFzm4ECC/dpkV2g7QCaxP6vWB
U3SToQh24wKBgQCSKaZqmQIeWnZoPbwQdV+PVAgkDYuQf/8FXbxJB+zOff1VPJXr
q02Wcst/jJqOoBfyQ5OE6aKDqMsxj1zQJAZTYLdPVz79rrhQ6U8vOR8xjwRmVuMg
c0X4sNWGzDTimJdqPL51eIA4ElilZplOevhncFHNHk+lmBCqULu4yj14XwKBgCcB
lSSgWMv/clyvGUv9PGESIPf1nsgdx28d2Nkvc3LBsfPGRdjo479wSColF9FAYGKF
V/XdaLu51aPqK4Gqn1fKTD36BcFQp68s4ot9ni0ppRwKJeuPiIawp366aGvSz9Un
F+tJT3XC8cU5PAPirIwPm5Rqh1Q7yIL6yKw0odqrAoGBAKo3vLaFhBizqUCuJrH9
uur7DAkvrqi5yyM/TwF2068Wn8ukjri36agEa1fW8WIwXz5Ki1VwT+9IiWXHak10
Aj1ha4amLOXn4Q7QcJ4LRooc27/njJG47PoW8E5gJlyXxeMlYh63nE1namfluS3l
WcFMdh8m8IBMofy5uMz1tCfE
-----END PRIVATE KEY-----
"""

tls_crt = b"""
-----BEGIN CERTIFICATE-----
MIIDfDCCAmSgAwIBAgIUaxbvOJvBhKlUzD15O0MYZWkUDUUwDQYJKoZIhvcNAQEL
BQAwRTELMAkGA1UEBhMCQVUxEzARBgNVBAgMClNvbWUtU3RhdGUxITAfBgNVBAoM
GEludGVybmV0IFdpZGdpdHMgUHR5IEx0ZDAeFw0yNDA5MTcxNjAwMjJaFw0zNDA5
MTUxNjAwMjJaMEUxCzAJBgNVBAYTAkFVMRMwEQYDVQQIDApTb21lLVN0YXRlMSEw
HwYDVQQKDBhJbnRlcm5ldCBXaWRnaXRzIFB0eSBMdGQwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQCZWdZm0pa6gjVGHJUu6m2vNefkaVL8NCDRDCUwpV2b
kgyeYVKpoLBRCy7DjM3Qoh4l9ZjFSXDN3KspBuFhpv1diNhBToJCAzvNOQXGHAgO
tn2KPAEjgrKHpNtolL4v4knzYSIGlOpRx1oDvZINLVtsJznYnN0lSAn+lxQ7miao
p3zypQrUMaWthNriUgxlPINgJwXXdFY8Q+nPi/Tzm0qG6BdgB3P7q3K2BsrVYEkZ
d/01dYQ7T//mneJneqjDxYXyId0K59XHVsZVNcwe7QDeefHfzh5uHFHqaWr1bZX8
mxC5q8YBu2AKwTnpyYBmSkvuHXSdro4oRcL1vgzDcdxtAgMBAAGjZDBiMB0GA1Ud
DgQWBBRbyJQDkTJtvim6fvy1EY7wGxSOhjAfBgNVHSMEGDAWgBRbyJQDkTJtvim6
fvy1EY7wGxSOhjAPBgNVHRMBAf8EBTADAQH/MA8GA1UdEQQIMAaHBH8AAAEwDQYJ
KoZIhvcNAQELBQADggEBAGBWZEjLSo4owfA8d+48KKhE6kN5/T/s0NHMLGMp4hUo
s8BI+K1d240ccALyi17/iwykaoo1pqAlTB+91U3EvEKu6CzvlPCcPcw1XCsLO8GR
3x8J5ejNqMuX2/9nNNbRqhdIjglaZaskjR56dKy0Jcz8tLO4pMGh/o4mNqVbBllC
u+P/F6icjciAqk4jAcjXupi1sHWCTgl1Nah/fbzLgTbgDXTraqYtF+dNWMP5AXaq
F8XRbbhy5aCZFOqmev41123OJWMBYp/xRMDi9l37BSNNemPfcTNWoo/v2F1ZN5RE
kV/ISkO3wm9WvgG7uM8u1ALcbz9mwugUkesVJwSws0k=
-----END CERTIFICATE-----
"""


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

from contextlib import ExitStack
from pathlib import Path

import pytest
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal

from .client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from .common import ObjectMeta
from .exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance
from .tls import TLSConfigV1Alpha1
from jumpstarter.common import MetadataFilter

pytestmark = pytest.mark.anyio


async def test_exporter_serve(mock_controller):
    exporter = ExporterConfigV1Alpha1(
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="ExporterConfig",
        metadata=ObjectMeta(namespace="default", name="test"),
        endpoint=mock_controller,
        token="dummy-exporter-token",
        export={
            "power": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_power.driver.MockPower",
            ),
            "nested": ExporterConfigV1Alpha1DriverInstance(
                children={
                    "tcp": ExporterConfigV1Alpha1DriverInstance(
                        type="jumpstarter_driver_network.driver.TcpNetwork",
                        config={
                            "host": "127.0.0.1",
                            "port": 8080,
                        },
                    )
                }
            ),
        },
    )

    client = ClientConfigV1Alpha1(
        name="testclient",
        metadata=ObjectMeta(namespace="default", name="testclient"),
        endpoint=mock_controller,
        token="dummy-client-token",
        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
        tls=TLSConfigV1Alpha1(insecure=True),
    )

    async with create_task_group() as tg:
        tg.start_soon(exporter.serve)

        with start_blocking_portal() as portal:
            async with client.lease_async(
                metadata_filter=MetadataFilter(),
                lease_name=None,
                portal=portal,
            ) as lease:
                with ExitStack() as stack:
                    async with lease.connect_async(stack) as client:
                        await client.power.call_async("on")
                        assert hasattr(client.nested, "tcp")

        tg.cancel_scope.cancel()


def test_exporter_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)

    path = tmp_path / "test.yaml"

    text = """apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: test
tls:
  ca: "cacertificatedata"
  insecure: true
endpoint: "jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

export:
  power:
    type: "jumpstarter_driver_power.driver.PduPower"
    config:
      host: "192.168.1.111"
      port: 1234
      auth:
          username: "admin"
          password: "secret"
  serial:
    type: "jumpstarter_driver_pyserial.driver.Pyserial"
    config:
      port: "/dev/ttyUSB0"
      baudrate: 115200
  nested:
    children:
      custom:
        type: "vendorpackage.CustomDriver"
        config:
          hello: "world"
"""
    path.write_text(
        text,
        encoding="utf-8",
    )

    config = ExporterConfigV1Alpha1.load("test")

    assert config == ExporterConfigV1Alpha1(
        alias="test",
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="ExporterConfig",
        metadata=ObjectMeta(namespace="default", name="test"),
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        tls=TLSConfigV1Alpha1(ca="cacertificatedata", insecure=True),
        export={
            "power": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_power.driver.PduPower",
                children={},  # missing children defaults to empty
                config={
                    "host": "192.168.1.111",
                    "port": 1234,
                    "auth": {
                        "username": "admin",
                        "password": "secret",
                    },
                },
            ),
            "serial": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_pyserial.driver.Pyserial",
                children={},
                config={
                    "port": "/dev/ttyUSB0",
                    "baudrate": 115200,
                },
            ),
            "nested": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter_driver_composite.driver.Composite",
                children={
                    "custom": ExporterConfigV1Alpha1DriverInstance(
                        type="vendorpackage.CustomDriver",
                        children={},
                        config={
                            "hello": "world",
                        },
                    )
                },
                config={},  # missing config defaults to empty
            ),
        },
        config={},
        path=path,
    )

    path.unlink()

    ExporterConfigV1Alpha1.save(config)

    assert config == ExporterConfigV1Alpha1.load("test")

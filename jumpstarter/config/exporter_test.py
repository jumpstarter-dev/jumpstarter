import pytest
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal

from jumpstarter.common import MetadataFilter

from .client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from .exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance

pytestmark = pytest.mark.anyio


async def test_exporter_serve(mock_controller):
    exporter = ExporterConfigV1Alpha1(
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="ExporterConfig",
        endpoint=mock_controller,
        token="dummy-exporter-token",
        export={
            "power": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter.drivers.power.driver.MockPower",
            ),
            "nested": ExporterConfigV1Alpha1DriverInstance(
                children={
                    "tcp": ExporterConfigV1Alpha1DriverInstance(
                        type="jumpstarter.drivers.network.driver.TcpNetwork",
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
        endpoint=mock_controller,
        token="dummy-client-token",
        drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
    )

    async with create_task_group() as tg:
        tg.start_soon(exporter.serve_forever)

        with start_blocking_portal() as portal:
            async with client.lease_async(metadata_filter=MetadataFilter(), portal=portal) as lease:
                async with lease.connect_async() as client:
                    assert await client.power.call_async("on") == "ok"
                    assert hasattr(client.nested, "tcp")

        tg.cancel_scope.cancel()


def test_exporter_config(monkeypatch, tmp_path):
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", tmp_path)

    path = tmp_path / "test.yaml"

    text = """apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig

endpoint: "jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

export:
  power:
    type: "jumpstarter.drivers.power.PduPower"
    config:
      host: "192.168.1.111"
      port: 1234
      auth:
          username: "admin"
          password: "secret"
  serial:
    type: "jumpstarter.drivers.serial.Pyserial"
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
        endpoint="jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        export={
            "power": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter.drivers.power.PduPower",
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
                type="jumpstarter.drivers.serial.Pyserial",
                children={},
                config={
                    "port": "/dev/ttyUSB0",
                    "baudrate": 115200,
                },
            ),
            "nested": ExporterConfigV1Alpha1DriverInstance(
                type="jumpstarter.drivers.composite.driver.Composite",
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
    )

    path.unlink()

    config.save()

    assert config == ExporterConfigV1Alpha1.load("test")

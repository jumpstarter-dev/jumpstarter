import grpc
import pytest
import yaml
from anyio import create_task_group
from anyio.from_thread import start_blocking_portal

from jumpstarter.client import LeaseRequest
from jumpstarter.common import MetadataFilter

from .exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance

pytestmark = pytest.mark.anyio


async def test_exporter_serve(mock_controller):
    exporter = ExporterConfigV1Alpha1(
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="Exporter",
        endpoint=mock_controller,
        token="dummy-exporter-token",
        export=ExporterConfigV1Alpha1DriverInstance(
            children={
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
        ),
    )

    async with create_task_group() as tg:
        tg.start_soon(exporter.serve)

        with start_blocking_portal() as portal:
            async with LeaseRequest(
                channel=grpc.aio.insecure_channel(mock_controller),
                metadata_filter=MetadataFilter(),
                portal=portal,
            ) as lease:
                async with lease.connect_async() as client:
                    assert await client.power.call_async("on") == "ok"
                    assert hasattr(client.nested, "tcp")

        tg.cancel_scope.cancel()


def test_exporter_config():
    config = """
apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter

endpoint: "grpcs://jumpstarter.my-lab.com:1443"
token: "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

export:
  children:
    power:
      type: "jumpstarter.drivers.power.PduPower"
      config:
        host: "192.168.1.111"
        port: 1234
        username: "admin"
        password: "secret"
    serial:
      type: "jumpstarter.drivers.power.PduPower"
      config:
        type: "jumpstarter.drivers.serial.Pyserial"
        port: "/dev/ttyUSB0"
        baudrate: 115200
    nested:
      children:
        custom:
          type: "vendorpackage.CustomDriver"
          config:
            hello: "world"
    """

    assert ExporterConfigV1Alpha1.model_validate(yaml.safe_load(config)) == ExporterConfigV1Alpha1(
        apiVersion="jumpstarter.dev/v1alpha1",
        kind="Exporter",
        endpoint="grpcs://jumpstarter.my-lab.com:1443",
        token="dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz",
        export=ExporterConfigV1Alpha1DriverInstance(
            type="jumpstarter.drivers.composite.driver.Composite",  # missing type defaults to Composite
            children={
                "power": ExporterConfigV1Alpha1DriverInstance(
                    type="jumpstarter.drivers.power.PduPower",
                    children={},  # missing children defaults to empty
                    config={
                        "host": "192.168.1.111",
                        "port": 1234,
                        "username": "admin",
                        "password": "secret",
                    },
                ),
                "serial": ExporterConfigV1Alpha1DriverInstance(
                    type="jumpstarter.drivers.power.PduPower",
                    children={},
                    config={
                        "type": "jumpstarter.drivers.serial.Pyserial",
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
        ),
    )

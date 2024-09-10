import yaml

from .exporter import ExporterConfigV1Alpha1, ExporterConfigV1Alpha1DriverInstance


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

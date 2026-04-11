from unittest.mock import AsyncMock, patch

from click.testing import CliRunner
from jumpstarter_kubernetes import (
    DriverInterfacesV1Alpha1Api,
    ExporterClassesV1Alpha1Api,
    V1Alpha1DriverImplementation,
    V1Alpha1DriverInterface,
    V1Alpha1DriverInterfaceList,
    V1Alpha1DriverInterfaceProto,
    V1Alpha1DriverInterfaceSpec,
    V1Alpha1DriverInterfaceStatus,
    V1Alpha1ExporterClass,
    V1Alpha1ExporterClassList,
    V1Alpha1ExporterClassSpec,
    V1Alpha1ExporterClassStatus,
    V1Alpha1InterfaceRequirement,
)
from jumpstarter_kubernetes.exporterclasses import V1Alpha1LabelSelector
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.client.models import V1ObjectMeta
from kubernetes_asyncio.config.config_exception import ConfigException

from jumpstarter_cli_admin.test_utils import json_equal

from .get import get


class MockResponse:
    status: int
    reason: str
    data: str

    def __init__(self, status: int, reason: str, body: str):
        self.status = status
        self.reason = reason
        self.data = body

    def getheaders(self):
        return {}


# --- DriverInterface test objects ---

TEST_DRIVER_INTERFACE = V1Alpha1DriverInterface(
    api_version="jumpstarter.dev/v1alpha1",
    kind="DriverInterface",
    metadata=V1ObjectMeta(name="dev-jumpstarter-power-v1", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
    spec=V1Alpha1DriverInterfaceSpec(
        proto=V1Alpha1DriverInterfaceProto(
            package="jumpstarter.interfaces.power.v1",
            descriptor="CpoBMQpwanVtcHN0YXJ0ZXI=",
        ),
        drivers=[
            V1Alpha1DriverImplementation(
                language="python",
                package="jumpstarter-driver-power",
                version="1.0.0",
                clientClass="jumpstarter_driver_power.client:PowerClient",
            ),
        ],
    ),
    status=V1Alpha1DriverInterfaceStatus(
        implementationCount=5,
    ),
)

TEST_DRIVER_INTERFACE_JSON = """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "DriverInterface",
    "metadata": {
        "creationTimestamp": "2024-01-01T21:00:00Z",
        "name": "dev-jumpstarter-power-v1",
        "namespace": "testing"
    },
    "spec": {
        "proto": {
            "package": "jumpstarter.interfaces.power.v1",
            "descriptor": "CpoBMQpwanVtcHN0YXJ0ZXI="
        },
        "drivers": [
            {
                "language": "python",
                "package": "jumpstarter-driver-power",
                "version": "1.0.0",
                "clientClass": "jumpstarter_driver_power.client:PowerClient"
            }
        ]
    },
    "status": {
        "implementationCount": 5
    }
}
"""


@patch.object(DriverInterfacesV1Alpha1Api, "get_driver_interface")
@patch.object(DriverInterfacesV1Alpha1Api, "_load_kube_config")
def test_get_driverinterface(_load_kube_config_mock, get_di_mock: AsyncMock):
    runner = CliRunner()

    # Get a single driverinterface — table output
    get_di_mock.return_value = TEST_DRIVER_INTERFACE
    result = runner.invoke(get, ["driverinterface", "dev-jumpstarter-power-v1"])
    assert result.exit_code == 0
    assert "dev-jumpstarter-power-v1" in result.output
    assert "jumpstarter.interfaces.power.v1" in result.output
    get_di_mock.reset_mock()

    # Get a single driverinterface — JSON output
    get_di_mock.return_value = TEST_DRIVER_INTERFACE
    result = runner.invoke(get, ["driverinterface", "dev-jumpstarter-power-v1", "--output", "json"])
    assert result.exit_code == 0
    assert json_equal(result.output, TEST_DRIVER_INTERFACE_JSON)
    get_di_mock.reset_mock()

    # Get a single driverinterface — name output
    get_di_mock.return_value = TEST_DRIVER_INTERFACE
    result = runner.invoke(get, ["driverinterface", "dev-jumpstarter-power-v1", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == "driverinterface.jumpstarter.dev/dev-jumpstarter-power-v1\n"
    get_di_mock.reset_mock()

    # Not found
    get_di_mock.side_effect = ApiException(
        http_resp=MockResponse(
            404,
            "Not Found",
            '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"driverinterfaces.jumpstarter.dev \\"dev-jumpstarter-power-v1\\" not found","reason":"NotFound","code":404}',  # noqa: E501
        )
    )
    result = runner.invoke(get, ["driverinterface", "dev-jumpstarter-power-v1"])
    assert result.exit_code == 1
    assert "NotFound" in result.output
    get_di_mock.reset_mock()


DRIVER_INTERFACE_LIST = V1Alpha1DriverInterfaceList(
    items=[
        TEST_DRIVER_INTERFACE,
        V1Alpha1DriverInterface(
            api_version="jumpstarter.dev/v1alpha1",
            kind="DriverInterface",
            metadata=V1ObjectMeta(name="dev-jumpstarter-serial-v1", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
            spec=V1Alpha1DriverInterfaceSpec(
                proto=V1Alpha1DriverInterfaceProto(package="jumpstarter.interfaces.serial.v1"),
            ),
            status=V1Alpha1DriverInterfaceStatus(implementationCount=3),
        ),
    ]
)


@patch.object(DriverInterfacesV1Alpha1Api, "list_driver_interfaces")
@patch.object(DriverInterfacesV1Alpha1Api, "_load_kube_config")
def test_get_driverinterfaces(_load_kube_config_mock, list_di_mock: AsyncMock):
    runner = CliRunner()

    list_di_mock.return_value = DRIVER_INTERFACE_LIST
    result = runner.invoke(get, ["driverinterfaces"])
    assert result.exit_code == 0
    assert "dev-jumpstarter-power-v1" in result.output
    assert "dev-jumpstarter-serial-v1" in result.output
    list_di_mock.reset_mock()

    # List via singular without name
    list_di_mock.return_value = DRIVER_INTERFACE_LIST
    result = runner.invoke(get, ["driverinterface"])
    assert result.exit_code == 0
    assert "dev-jumpstarter-power-v1" in result.output
    list_di_mock.reset_mock()


@patch.object(DriverInterfacesV1Alpha1Api, "get_driver_interface")
@patch.object(DriverInterfacesV1Alpha1Api, "_load_kube_config")
def test_get_driverinterface_config_error(_load_kube_config_mock, get_di_mock: AsyncMock):
    runner = CliRunner()

    get_di_mock.side_effect = ConfigException("Invalid kubeconfig")
    result = runner.invoke(get, ["driverinterface", "test"])
    assert result.exit_code == 1


# --- ExporterClass test objects ---

TEST_EXPORTER_CLASS = V1Alpha1ExporterClass(
    api_version="jumpstarter.dev/v1alpha1",
    kind="ExporterClass",
    metadata=V1ObjectMeta(name="embedded-linux-board", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
    spec=V1Alpha1ExporterClassSpec(
        selector=V1Alpha1LabelSelector(matchLabels={"board": "embedded-linux"}),
        interfaces=[
            V1Alpha1InterfaceRequirement(name="power", interfaceRef="dev-jumpstarter-power-v1", required=True),
            V1Alpha1InterfaceRequirement(name="serial", interfaceRef="dev-jumpstarter-serial-v1", required=True),
            V1Alpha1InterfaceRequirement(name="network", interfaceRef="dev-jumpstarter-network-v1", required=False),
        ],
    ),
    status=V1Alpha1ExporterClassStatus(
        satisfiedExporterCount=3,
        resolvedInterfaces=["dev-jumpstarter-power-v1", "dev-jumpstarter-serial-v1", "dev-jumpstarter-network-v1"],
    ),
)

TEST_EXPORTER_CLASS_JSON = """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "ExporterClass",
    "metadata": {
        "creationTimestamp": "2024-01-01T21:00:00Z",
        "name": "embedded-linux-board",
        "namespace": "testing"
    },
    "spec": {
        "selector": {
            "matchLabels": {
                "board": "embedded-linux"
            }
        },
        "interfaces": [
            {
                "name": "power",
                "interfaceRef": "dev-jumpstarter-power-v1",
                "required": true
            },
            {
                "name": "serial",
                "interfaceRef": "dev-jumpstarter-serial-v1",
                "required": true
            },
            {
                "name": "network",
                "interfaceRef": "dev-jumpstarter-network-v1",
                "required": false
            }
        ]
    },
    "status": {
        "satisfiedExporterCount": 3,
        "resolvedInterfaces": [
            "dev-jumpstarter-power-v1",
            "dev-jumpstarter-serial-v1",
            "dev-jumpstarter-network-v1"
        ]
    }
}
"""


@patch.object(ExporterClassesV1Alpha1Api, "get_exporter_class")
@patch.object(ExporterClassesV1Alpha1Api, "_load_kube_config")
def test_get_exporterclass(_load_kube_config_mock, get_ec_mock: AsyncMock):
    runner = CliRunner()

    # Get a single exporterclass — table output
    get_ec_mock.return_value = TEST_EXPORTER_CLASS
    result = runner.invoke(get, ["exporterclass", "embedded-linux-board"])
    assert result.exit_code == 0
    assert "embedded-linux-board" in result.output
    get_ec_mock.reset_mock()

    # Get a single exporterclass — JSON output
    get_ec_mock.return_value = TEST_EXPORTER_CLASS
    result = runner.invoke(get, ["exporterclass", "embedded-linux-board", "--output", "json"])
    assert result.exit_code == 0
    assert json_equal(result.output, TEST_EXPORTER_CLASS_JSON)
    get_ec_mock.reset_mock()

    # Get a single exporterclass — name output
    get_ec_mock.return_value = TEST_EXPORTER_CLASS
    result = runner.invoke(get, ["exporterclass", "embedded-linux-board", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == "exporterclass.jumpstarter.dev/embedded-linux-board\n"
    get_ec_mock.reset_mock()

    # Not found
    get_ec_mock.side_effect = ApiException(
        http_resp=MockResponse(
            404,
            "Not Found",
            '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"exporterclasses.jumpstarter.dev \\"embedded-linux-board\\" not found","reason":"NotFound","code":404}',  # noqa: E501
        )
    )
    result = runner.invoke(get, ["exporterclass", "embedded-linux-board"])
    assert result.exit_code == 1
    assert "NotFound" in result.output
    get_ec_mock.reset_mock()


EXPORTER_CLASS_LIST = V1Alpha1ExporterClassList(
    items=[
        TEST_EXPORTER_CLASS,
        V1Alpha1ExporterClass(
            api_version="jumpstarter.dev/v1alpha1",
            kind="ExporterClass",
            metadata=V1ObjectMeta(name="basic-board", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
            spec=V1Alpha1ExporterClassSpec(
                extends="embedded-linux-board",
                interfaces=[
                    V1Alpha1InterfaceRequirement(name="power", interfaceRef="dev-jumpstarter-power-v1", required=True),
                ],
            ),
            status=V1Alpha1ExporterClassStatus(satisfiedExporterCount=1),
        ),
    ]
)


@patch.object(ExporterClassesV1Alpha1Api, "list_exporter_classes")
@patch.object(ExporterClassesV1Alpha1Api, "_load_kube_config")
def test_get_exporterclasses(_load_kube_config_mock, list_ec_mock: AsyncMock):
    runner = CliRunner()

    list_ec_mock.return_value = EXPORTER_CLASS_LIST
    result = runner.invoke(get, ["exporterclasses"])
    assert result.exit_code == 0
    assert "embedded-linux-board" in result.output
    assert "basic-board" in result.output
    list_ec_mock.reset_mock()

    # List via singular without name
    list_ec_mock.return_value = EXPORTER_CLASS_LIST
    result = runner.invoke(get, ["exporterclass"])
    assert result.exit_code == 0
    assert "embedded-linux-board" in result.output
    list_ec_mock.reset_mock()


@patch.object(ExporterClassesV1Alpha1Api, "get_exporter_class")
@patch.object(ExporterClassesV1Alpha1Api, "_load_kube_config")
def test_get_exporterclass_config_error(_load_kube_config_mock, get_ec_mock: AsyncMock):
    runner = CliRunner()

    get_ec_mock.side_effect = ConfigException("Invalid kubeconfig")
    result = runner.invoke(get, ["exporterclass", "test"])
    assert result.exit_code == 1

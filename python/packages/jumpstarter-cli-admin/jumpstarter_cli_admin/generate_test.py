from unittest.mock import MagicMock, patch

import yaml
from click.testing import CliRunner

from .generate import _to_crd_name, generate


def test_to_crd_name_standard():
    """Test standard jumpstarter.interfaces.X.vN conversion."""
    assert _to_crd_name("jumpstarter.interfaces.power.v1") == "dev-jumpstarter-power-v1"
    assert _to_crd_name("jumpstarter.interfaces.serial.v1") == "dev-jumpstarter-serial-v1"
    assert _to_crd_name("jumpstarter.interfaces.storage.v2") == "dev-jumpstarter-storage-v2"


def test_to_crd_name_nested():
    """Test nested package names."""
    assert _to_crd_name("jumpstarter.interfaces.network.ethernet.v1") == "dev-jumpstarter-network-ethernet-v1"


def test_to_crd_name_fallback():
    """Test fallback for non-standard package names."""
    result = _to_crd_name("com.example.custom")
    # Should replace dots with dashes and strip invalid chars.
    assert "." not in result
    assert result == "com-example-custom"


def test_generate_driverinterface_basic():
    """Test the generate driverinterface command produces valid YAML."""
    mock_fd = MagicMock()
    mock_fd.package = "jumpstarter.interfaces.power.v1"
    mock_fd.SerializeToString.return_value = b"\x12\x1fjumpstarter.interfaces.power.v1"

    mock_cls = MagicMock()
    mock_cls.client.return_value = "jumpstarter_driver_power.client:PowerClient"

    with (
        patch("jumpstarter_cli_admin.generate._resolve_class", return_value=mock_cls),
        patch("jumpstarter_cli_admin.generate.build_file_descriptor", return_value=mock_fd),
    ):
        runner = CliRunner()
        result = runner.invoke(
            generate,
            [
                "driverinterface",
                "jumpstarter_driver_power.driver.PowerInterface",
                "--driver-package",
                "jumpstarter-driver-power",
                "--driver-version",
                "1.0.0",
                "--namespace",
                "lab-detroit",
            ],
        )
        assert result.exit_code == 0

        # Parse the YAML output.
        output = yaml.safe_load(result.output)
        assert output["apiVersion"] == "jumpstarter.dev/v1alpha1"
        assert output["kind"] == "DriverInterface"
        assert output["metadata"]["name"] == "dev-jumpstarter-power-v1"
        assert output["metadata"]["namespace"] == "lab-detroit"
        assert output["spec"]["proto"]["package"] == "jumpstarter.interfaces.power.v1"
        assert "descriptor" in output["spec"]["proto"]

        # Check driver info.
        drivers = output["spec"]["drivers"]
        assert len(drivers) == 1
        assert drivers[0]["language"] == "python"
        assert drivers[0]["package"] == "jumpstarter-driver-power"
        assert drivers[0]["version"] == "1.0.0"
        assert drivers[0]["clientClass"] == "jumpstarter_driver_power.client:PowerClient"


def test_generate_driverinterface_custom_name():
    """Test overriding the CRD name."""
    mock_fd = MagicMock()
    mock_fd.package = "jumpstarter.interfaces.power.v1"
    mock_fd.SerializeToString.return_value = b"\x12\x1fjumpstarter.interfaces.power.v1"

    mock_cls = MagicMock()
    mock_cls.client.side_effect = Exception("no client")

    with (
        patch("jumpstarter_cli_admin.generate._resolve_class", return_value=mock_cls),
        patch("jumpstarter_cli_admin.generate.build_file_descriptor", return_value=mock_fd),
    ):
        runner = CliRunner()
        result = runner.invoke(
            generate,
            [
                "driverinterface",
                "my_module.MyInterface",
                "--name",
                "my-custom-name",
            ],
        )
        assert result.exit_code == 0

        output = yaml.safe_load(result.output)
        assert output["metadata"]["name"] == "my-custom-name"
        # No drivers section when no driver package specified.
        assert "drivers" not in output["spec"]


def test_generate_driverinterface_no_namespace():
    """Test that namespace is omitted when not provided."""
    mock_fd = MagicMock()
    mock_fd.package = "jumpstarter.interfaces.serial.v1"
    mock_fd.SerializeToString.return_value = b"\x12\x20jumpstarter.interfaces.serial.v1"

    mock_cls = MagicMock()
    mock_cls.client.side_effect = Exception("no client")

    with (
        patch("jumpstarter_cli_admin.generate._resolve_class", return_value=mock_cls),
        patch("jumpstarter_cli_admin.generate.build_file_descriptor", return_value=mock_fd),
    ):
        runner = CliRunner()
        result = runner.invoke(
            generate,
            ["driverinterface", "my_module.SerialInterface"],
        )
        assert result.exit_code == 0

        output = yaml.safe_load(result.output)
        assert "namespace" not in output["metadata"]

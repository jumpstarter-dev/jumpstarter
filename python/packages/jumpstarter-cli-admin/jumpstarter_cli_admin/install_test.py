from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from jumpstarter_cli_admin.install import (
    _configure_endpoints,
    _create_kind_cluster,
    _create_minikube_cluster,
    _delete_kind_cluster,
    _delete_minikube_cluster,
    _handle_cluster_creation,
    _handle_cluster_deletion,
    _validate_cluster_type,
    _validate_prerequisites,
    get_ip_generic,
    install,
    uninstall,
)


class TestValidationFunctions:
    """Test validation helper functions."""

    @patch("jumpstarter_cli_admin.install.helm_installed")
    def test_validate_prerequisites_helm_installed(self, mock_helm_installed):
        mock_helm_installed.return_value = True
        # Should not raise any exception
        _validate_prerequisites("helm")

    @patch("jumpstarter_cli_admin.install.helm_installed")
    def test_validate_prerequisites_helm_not_installed(self, mock_helm_installed):
        mock_helm_installed.return_value = False
        with pytest.raises(click.ClickException, match="helm is not installed"):
            _validate_prerequisites("helm")

    def test_validate_cluster_type_both_specified(self):
        with pytest.raises(click.ClickException, match="You can only select one local cluster type"):
            _validate_cluster_type("kind", "minikube")

    def test_validate_cluster_type_kind_only(self):
        result = _validate_cluster_type("kind", None)
        assert result == "kind"

    def test_validate_cluster_type_minikube_only(self):
        result = _validate_cluster_type(None, "minikube")
        assert result == "minikube"

    def test_validate_cluster_type_neither(self):
        result = _validate_cluster_type(None, None)
        assert result is None


class TestEndpointConfiguration:
    """Test endpoint configuration logic."""

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.get_ip_generic")
    async def test_configure_endpoints_all_defaults(self, mock_get_ip):
        mock_get_ip.return_value = "192.168.1.100"

        ip, basedomain, grpc_endpoint, router_endpoint = await _configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain=None,
            grpc_endpoint=None,
            router_endpoint=None,
        )

        assert ip == "192.168.1.100"
        assert basedomain == "jumpstarter.192.168.1.100.nip.io"
        assert grpc_endpoint == "grpc.jumpstarter.192.168.1.100.nip.io:8082"
        assert router_endpoint == "router.jumpstarter.192.168.1.100.nip.io:8083"

    @pytest.mark.asyncio
    async def test_configure_endpoints_all_provided(self):
        ip, basedomain, grpc_endpoint, router_endpoint = await _configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip="10.0.0.1",
            basedomain="example.com",
            grpc_endpoint="grpc.example.com:9000",
            router_endpoint="router.example.com:9001",
        )

        assert ip == "10.0.0.1"
        assert basedomain == "example.com"
        assert grpc_endpoint == "grpc.example.com:9000"
        assert router_endpoint == "router.example.com:9001"


class TestClusterCreation:
    """Test cluster creation logic."""

    @pytest.mark.asyncio
    async def test_handle_cluster_creation_not_requested(self):
        # Should return early without doing anything
        await _handle_cluster_creation(
            create_cluster=False,
            cluster_type=None,
            force_recreate_cluster=False,
            cluster_name="test",
            kind_extra_args="",
            minikube_extra_args="",
            kind="kind",
            minikube="minikube",
        )

    @pytest.mark.asyncio
    async def test_handle_cluster_creation_no_cluster_type(self):
        with pytest.raises(click.ClickException, match="--create-cluster requires either --kind or --minikube"):
            await _handle_cluster_creation(
                create_cluster=True,
                cluster_type=None,
                force_recreate_cluster=False,
                cluster_name="test",
                kind_extra_args="",
                minikube_extra_args="",
                kind="kind",
                minikube="minikube",
            )

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._create_kind_cluster")
    async def test_handle_cluster_creation_kind(self, mock_create_kind):
        await _handle_cluster_creation(
            create_cluster=True,
            cluster_type="kind",
            force_recreate_cluster=False,
            cluster_name="test-cluster",
            kind_extra_args="--verbosity=1",
            minikube_extra_args="",
            kind="kind",
            minikube="minikube",
        )

        mock_create_kind.assert_called_once_with("kind", "test-cluster", "--verbosity=1", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._create_minikube_cluster")
    async def test_handle_cluster_creation_minikube(self, mock_create_minikube):
        await _handle_cluster_creation(
            create_cluster=True,
            cluster_type="minikube",
            force_recreate_cluster=False,
            cluster_name="test-cluster",
            kind_extra_args="",
            minikube_extra_args="--memory=4096",
            kind="kind",
            minikube="minikube",
        )

        mock_create_minikube.assert_called_once_with("minikube", "test-cluster", "--memory=4096", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.click.confirm")
    @patch("jumpstarter_cli_admin.install._create_kind_cluster")
    async def test_handle_cluster_creation_force_recreate_confirmed(self, mock_create_kind, mock_confirm):
        mock_confirm.return_value = True

        await _handle_cluster_creation(
            create_cluster=True,
            cluster_type="kind",
            force_recreate_cluster=True,
            cluster_name="test-cluster",
            kind_extra_args="",
            minikube_extra_args="",
            kind="kind",
            minikube="minikube",
        )

        mock_confirm.assert_called_once()
        mock_create_kind.assert_called_once_with("kind", "test-cluster", "", True)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.click.confirm")
    async def test_handle_cluster_creation_force_recreate_cancelled(self, mock_confirm):
        mock_confirm.return_value = False

        with pytest.raises(click.Abort):
            await _handle_cluster_creation(
                create_cluster=True,
                cluster_type="kind",
                force_recreate_cluster=True,
                cluster_name="test-cluster",
                kind_extra_args="",
                minikube_extra_args="",
                kind="kind",
                minikube="minikube",
            )


class TestSpecificClusterCreation:
    """Test specific cluster creation functions."""

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    @patch("jumpstarter_cli_admin.install.create_kind_cluster")
    async def test_create_kind_cluster_success(self, mock_create_kind, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_create_kind.return_value = True

        await _create_kind_cluster("kind", "test-cluster", "--verbosity=1", False)

        mock_create_kind.assert_called_once_with("kind", "test-cluster", ["--verbosity=1"], False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    async def test_create_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(click.ClickException, match="kind is not installed"):
            await _create_kind_cluster("kind", "test-cluster", "", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    @patch("jumpstarter_cli_admin.install.create_kind_cluster")
    async def test_create_kind_cluster_failure(self, mock_create_kind, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_create_kind.side_effect = RuntimeError("Creation failed")

        with pytest.raises(click.ClickException, match="Failed to create Kind cluster"):
            await _create_kind_cluster("kind", "test-cluster", "", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.minikube_installed")
    @patch("jumpstarter_cli_admin.install.create_minikube_cluster")
    async def test_create_minikube_cluster_success(self, mock_create_minikube, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_create_minikube.return_value = True

        await _create_minikube_cluster("minikube", "test-cluster", "--memory=4096", False)

        mock_create_minikube.assert_called_once_with("minikube", "test-cluster", ["--memory=4096"], False)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.minikube_installed")
    async def test_create_minikube_cluster_not_installed(self, mock_minikube_installed):
        mock_minikube_installed.return_value = False

        with pytest.raises(click.ClickException, match="minikube is not installed"):
            await _create_minikube_cluster("minikube", "test-cluster", "", False)


class TestIPDetection:
    """Test IP address detection functions."""

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.get_minikube_ip")
    @patch("jumpstarter_cli_admin.install.get_ip_address")
    async def test_get_ip_generic_minikube(self, mock_get_ip_address, mock_get_minikube_ip):
        mock_get_minikube_ip.return_value = "192.168.49.2"

        result = await get_ip_generic("minikube", "minikube", "test-cluster")

        assert result == "192.168.49.2"
        mock_get_minikube_ip.assert_called_once_with("test-cluster", "minikube")
        mock_get_ip_address.assert_not_called()

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.get_ip_address")
    async def test_get_ip_generic_kind(self, mock_get_ip_address):
        mock_get_ip_address.return_value = "192.168.1.100"

        result = await get_ip_generic("kind", "minikube", "test-cluster")

        assert result == "192.168.1.100"
        mock_get_ip_address.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.get_ip_address")
    async def test_get_ip_generic_none(self, mock_get_ip_address):
        mock_get_ip_address.return_value = "192.168.1.100"

        result = await get_ip_generic(None, "minikube", "test-cluster")

        assert result == "192.168.1.100"
        mock_get_ip_address.assert_called_once()


class TestInstallCommand:
    """Test the main install CLI command."""

    def setup_method(self):
        self.runner = CliRunner()

    @patch("jumpstarter_cli_admin.install.helm_installed")
    def test_install_command_helm_not_installed(self, mock_helm_installed):
        mock_helm_installed.return_value = False

        result = self.runner.invoke(install, [])

        assert result.exit_code != 0
        assert "helm is not installed" in result.output

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install._configure_endpoints")
    @patch("jumpstarter_cli_admin.install._handle_cluster_creation")
    @patch("jumpstarter_cli_admin.install.install_helm_chart")
    @patch("jumpstarter_cli_admin.install.get_latest_compatible_controller_version")
    def test_install_command_success_minimal(
        self,
        mock_get_version,
        mock_install_helm,
        mock_handle_cluster,
        mock_configure_endpoints,
        mock_validate_cluster,
        mock_helm_installed,
    ):
        mock_helm_installed.return_value = True
        mock_validate_cluster.return_value = None
        mock_configure_endpoints.return_value = (
            "192.168.1.100",
            "jumpstarter.192.168.1.100.nip.io",
            "grpc.jumpstarter.192.168.1.100.nip.io:8082",
            "router.jumpstarter.192.168.1.100.nip.io:8083",
        )
        mock_get_version.return_value = "1.0.0"
        mock_install_helm.return_value = None

        result = self.runner.invoke(install, [])

        assert result.exit_code == 0
        mock_install_helm.assert_called_once()

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    def test_install_command_both_cluster_types(self, mock_validate_cluster, mock_helm_installed):
        mock_helm_installed.return_value = True
        mock_validate_cluster.side_effect = click.ClickException("You can only select one local cluster type")

        result = self.runner.invoke(install, ["--kind", "kind", "--minikube", "minikube"])

        assert result.exit_code != 0
        assert "You can only select one local cluster type" in result.output

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install._configure_endpoints")
    @patch("jumpstarter_cli_admin.install._handle_cluster_creation")
    @patch("jumpstarter_cli_admin.install.install_helm_chart")
    @patch("jumpstarter_cli_admin.install.get_latest_compatible_controller_version")
    def test_install_command_with_kind_create_cluster(
        self,
        mock_get_version,
        mock_install_helm,
        mock_handle_cluster,
        mock_configure_endpoints,
        mock_validate_cluster,
        mock_helm_installed,
    ):
        mock_helm_installed.return_value = True
        mock_validate_cluster.return_value = "kind"
        mock_configure_endpoints.return_value = (
            "192.168.1.100",
            "jumpstarter.192.168.1.100.nip.io",
            "grpc.jumpstarter.192.168.1.100.nip.io:8082",
            "router.jumpstarter.192.168.1.100.nip.io:8083",
        )
        mock_get_version.return_value = "1.0.0"
        mock_install_helm.return_value = None

        result = self.runner.invoke(install, ["--kind", "kind", "--create-cluster"])

        assert result.exit_code == 0
        mock_handle_cluster.assert_called_once()
        # Verify cluster creation was called with correct parameters
        args = mock_handle_cluster.call_args[0]  # positional args
        assert args[0] is True  # create_cluster
        assert args[1] == "kind"  # cluster_type

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install._configure_endpoints")
    @patch("jumpstarter_cli_admin.install._handle_cluster_creation")
    @patch("jumpstarter_cli_admin.install.install_helm_chart")
    @patch("jumpstarter_cli_admin.install.get_latest_compatible_controller_version")
    def test_install_command_with_custom_options(
        self,
        mock_get_version,
        mock_install_helm,
        mock_handle_cluster,
        mock_configure_endpoints,
        mock_validate_cluster,
        mock_helm_installed,
    ):
        mock_helm_installed.return_value = True
        mock_validate_cluster.return_value = "minikube"
        mock_configure_endpoints.return_value = (
            "10.0.0.1",
            "custom.example.com",
            "grpc.custom.example.com:9000",
            "router.custom.example.com:9001",
        )
        mock_get_version.return_value = "1.0.0"
        mock_install_helm.return_value = None

        result = self.runner.invoke(
            install,
            [
                "--minikube",
                "minikube",
                "--create-cluster",
                "--cluster-name",
                "custom-cluster",
                "--force-recreate-cluster",
                "--ip",
                "10.0.0.1",
                "--basedomain",
                "custom.example.com",
                "--grpc-endpoint",
                "grpc.custom.example.com:9000",
                "--router-endpoint",
                "router.custom.example.com:9001",
                "--minikube-extra-args",
                "--memory=4096",
            ],
        )

        assert result.exit_code == 0

        # Verify cluster creation was called with custom parameters
        cluster_args = mock_handle_cluster.call_args[0]  # positional args
        assert cluster_args[3] == "custom-cluster"  # cluster_name
        assert cluster_args[2] is True  # force_recreate_cluster
        assert cluster_args[5] == "--memory=4096"  # minikube_extra_args

        # Verify endpoint configuration was called with custom values
        endpoint_args = mock_configure_endpoints.call_args[0]  # positional args
        assert endpoint_args[2] == "custom-cluster"  # cluster_name

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install._configure_endpoints")
    @patch("jumpstarter_cli_admin.install._handle_cluster_creation")
    @patch("jumpstarter_cli_admin.install.install_helm_chart")
    @patch("jumpstarter_cli_admin.install.get_latest_compatible_controller_version")
    def test_install_command_helm_installation_failure(
        self,
        mock_get_version,
        mock_install_helm,
        mock_handle_cluster,
        mock_configure_endpoints,
        mock_validate_cluster,
        mock_helm_installed,
    ):
        mock_helm_installed.return_value = True
        mock_validate_cluster.return_value = None
        mock_configure_endpoints.return_value = (
            "192.168.1.100",
            "jumpstarter.192.168.1.100.nip.io",
            "grpc.jumpstarter.192.168.1.100.nip.io:8082",
            "router.jumpstarter.192.168.1.100.nip.io:8083",
        )
        mock_get_version.return_value = "1.0.0"
        mock_install_helm.side_effect = RuntimeError("Helm installation failed")

        result = self.runner.invoke(install, [])

        assert result.exit_code != 0
        assert result.exception  # Should have an exception

    def test_install_command_help(self):
        result = self.runner.invoke(install, ["--help"])

        assert result.exit_code == 0
        assert "Install Jumpstarter" in result.output or "Usage:" in result.output
        assert "--create-cluster" in result.output
        assert "--kind" in result.output
        assert "--minikube" in result.output


class TestClusterDeletion:
    """Test cluster deletion logic."""

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    @patch("jumpstarter_cli_admin.install.delete_kind_cluster")
    async def test_delete_kind_cluster_success(self, mock_delete_kind, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_delete_kind.return_value = True

        await _delete_kind_cluster("kind", "test-cluster")

        mock_delete_kind.assert_called_once_with("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    async def test_delete_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(click.ClickException, match="kind is not installed"):
            await _delete_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.kind_installed")
    @patch("jumpstarter_cli_admin.install.delete_kind_cluster")
    async def test_delete_kind_cluster_failure(self, mock_delete_kind, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_delete_kind.side_effect = RuntimeError("Deletion failed")

        with pytest.raises(click.ClickException, match="Failed to delete Kind cluster"):
            await _delete_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.minikube_installed")
    @patch("jumpstarter_cli_admin.install.delete_minikube_cluster")
    async def test_delete_minikube_cluster_success(self, mock_delete_minikube, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_delete_minikube.return_value = True

        await _delete_minikube_cluster("minikube", "test-cluster")

        mock_delete_minikube.assert_called_once_with("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.minikube_installed")
    async def test_delete_minikube_cluster_not_installed(self, mock_minikube_installed):
        mock_minikube_installed.return_value = False

        with pytest.raises(click.ClickException, match="minikube is not installed"):
            await _delete_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install.minikube_installed")
    @patch("jumpstarter_cli_admin.install.delete_minikube_cluster")
    async def test_delete_minikube_cluster_failure(self, mock_delete_minikube, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_delete_minikube.side_effect = RuntimeError("Deletion failed")

        with pytest.raises(click.ClickException, match="Failed to delete Minikube cluster"):
            await _delete_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    async def test_handle_cluster_deletion_no_cluster_type(self, mock_validate_cluster):
        mock_validate_cluster.return_value = None

        # Should return early without doing anything
        await _handle_cluster_deletion(None, None, "test-cluster")

        mock_validate_cluster.assert_called_once_with(None, None)

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install.click.confirm")
    @patch("jumpstarter_cli_admin.install._delete_kind_cluster")
    async def test_handle_cluster_deletion_kind_confirmed(self, mock_delete_kind, mock_confirm, mock_validate_cluster):
        mock_validate_cluster.return_value = "kind"
        mock_confirm.return_value = True

        await _handle_cluster_deletion("kind", None, "test-cluster")

        mock_confirm.assert_called_once()
        mock_delete_kind.assert_called_once_with("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install.click.confirm")
    async def test_handle_cluster_deletion_cancelled(self, mock_confirm, mock_validate_cluster):
        mock_validate_cluster.return_value = "kind"
        mock_confirm.return_value = False

        await _handle_cluster_deletion("kind", None, "test-cluster")

        mock_confirm.assert_called_once()
        # No deletion should occur

    @pytest.mark.asyncio
    @patch("jumpstarter_cli_admin.install._validate_cluster_type")
    @patch("jumpstarter_cli_admin.install.click.confirm")
    @patch("jumpstarter_cli_admin.install._delete_minikube_cluster")
    async def test_handle_cluster_deletion_minikube_confirmed(
        self, mock_delete_minikube, mock_confirm, mock_validate_cluster
    ):
        mock_validate_cluster.return_value = "minikube"
        mock_confirm.return_value = True

        await _handle_cluster_deletion(None, "minikube", "test-cluster")

        mock_confirm.assert_called_once()
        mock_delete_minikube.assert_called_once_with("minikube", "test-cluster")


class TestUninstallCommand:
    """Test the main uninstall CLI command."""

    def setup_method(self):
        self.runner = CliRunner()

    @patch("jumpstarter_cli_admin.install.helm_installed")
    def test_uninstall_command_helm_not_installed(self, mock_helm_installed):
        mock_helm_installed.return_value = False

        result = self.runner.invoke(uninstall, [])

        assert result.exit_code != 0
        assert "helm is not installed" in result.output

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install.uninstall_helm_chart")
    def test_uninstall_command_success_minimal(self, mock_uninstall_helm, mock_helm_installed):
        mock_helm_installed.return_value = True
        mock_uninstall_helm.return_value = None

        result = self.runner.invoke(uninstall, [])

        assert result.exit_code == 0
        mock_uninstall_helm.assert_called_once_with("jumpstarter", "jumpstarter-lab", None, None, "helm")

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install.uninstall_helm_chart")
    @patch("jumpstarter_cli_admin.install._handle_cluster_deletion")
    def test_uninstall_command_with_cluster_deletion(
        self, mock_handle_deletion, mock_uninstall_helm, mock_helm_installed
    ):
        mock_helm_installed.return_value = True
        mock_uninstall_helm.return_value = None

        result = self.runner.invoke(uninstall, ["--delete-cluster", "--kind", "kind"])

        assert result.exit_code == 0
        mock_uninstall_helm.assert_called_once()
        mock_handle_deletion.assert_called_once_with("kind", None, "jumpstarter-lab")

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install.uninstall_helm_chart")
    @patch("jumpstarter_cli_admin.install._handle_cluster_deletion")
    def test_uninstall_command_with_custom_options(
        self, mock_handle_deletion, mock_uninstall_helm, mock_helm_installed
    ):
        mock_helm_installed.return_value = True
        mock_uninstall_helm.return_value = None

        result = self.runner.invoke(
            uninstall,
            [
                "--helm",
                "custom-helm",
                "--name",
                "custom-name",
                "--namespace",
                "custom-namespace",
                "--delete-cluster",
                "--minikube",
                "custom-minikube",
                "--cluster-name",
                "custom-cluster",
            ],
        )

        assert result.exit_code == 0
        mock_uninstall_helm.assert_called_once_with("custom-name", "custom-namespace", None, None, "custom-helm")
        mock_handle_deletion.assert_called_once_with(None, "custom-minikube", "custom-cluster")

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install.uninstall_helm_chart")
    def test_uninstall_command_helm_failure(self, mock_uninstall_helm, mock_helm_installed):
        mock_helm_installed.return_value = True
        mock_uninstall_helm.side_effect = RuntimeError("Uninstall failed")

        result = self.runner.invoke(uninstall, [])

        assert result.exit_code != 0
        assert result.exception  # Should have an exception

    @patch("jumpstarter_cli_admin.install.helm_installed")
    @patch("jumpstarter_cli_admin.install.uninstall_helm_chart")
    @patch("jumpstarter_cli_admin.install._handle_cluster_deletion")
    def test_uninstall_command_cluster_deletion_only(
        self, mock_handle_deletion, mock_uninstall_helm, mock_helm_installed
    ):
        mock_helm_installed.return_value = True
        mock_uninstall_helm.return_value = None

        result = self.runner.invoke(uninstall, ["--delete-cluster", "--kind", "kind", "--cluster-name", "test-cluster"])

        assert result.exit_code == 0
        mock_handle_deletion.assert_called_once_with("kind", None, "test-cluster")

    def test_uninstall_command_help(self):
        result = self.runner.invoke(uninstall, ["--help"])

        assert result.exit_code == 0
        assert "Uninstall" in result.output or "Usage:" in result.output
        assert "--delete-cluster" in result.output
        assert "--kind" in result.output
        assert "--minikube" in result.output

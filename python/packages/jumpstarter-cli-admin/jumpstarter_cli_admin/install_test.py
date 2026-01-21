from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner
from jumpstarter_kubernetes.callbacks import SilentCallback
from jumpstarter_kubernetes.cluster.kind import create_kind_cluster_with_options
from jumpstarter_kubernetes.cluster.minikube import create_minikube_cluster_with_options
from jumpstarter_kubernetes.cluster.operations import validate_cluster_type_selection

from jumpstarter_cli_admin.install import (
    _validate_prerequisites,
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
        """Test that error is raised when both kind and minikube are specified."""
        from jumpstarter_kubernetes.exceptions import ClusterTypeValidationError

        with pytest.raises(
            ClusterTypeValidationError, match='You can only select one local cluster type "kind" or "minikube"'
        ):
            validate_cluster_type_selection("kind", "minikube")

    def test_validate_cluster_type_kind_only(self):
        """Test that 'kind' is returned when only kind is specified."""
        result = validate_cluster_type_selection("kind", None)
        assert result == "kind"

    def test_validate_cluster_type_minikube_only(self):
        """Test that 'minikube' is returned when only minikube is specified."""
        result = validate_cluster_type_selection(None, "minikube")
        assert result == "minikube"

    # Note: test_validate_cluster_type_auto_detect removed as this function
    # is now tested in the jumpstarter-kubernetes library


class TestEndpointConfiguration:
    """Test endpoint configuration functions."""

    # Note: test_configure_endpoints_minikube removed as this function
    # is now tested in the jumpstarter-kubernetes library


class TestClusterCreation:
    """Test cluster creation functions."""

    # Note: Tests for _handle_cluster_creation, _create_kind_cluster, and _create_minikube_cluster
    # have been removed as these functions no longer exist after the refactoring.
    # The functionality is now tested through the new create_kind_cluster_with_options
    # and create_minikube_cluster_with_options functions in their respective modules.

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.create_kind_cluster")
    async def test_create_kind_cluster_with_options_success(self, mock_create_kind, mock_kind_installed):
        """Test creating a Kind cluster with the new function structure."""

        mock_kind_installed.return_value = True
        mock_create_kind.return_value = True
        callback = SilentCallback()

        await create_kind_cluster_with_options(
            "kind", "test-cluster", "--verbosity=1", False, None, callback
        )

        mock_create_kind.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    async def test_create_kind_cluster_with_options_not_installed(self, mock_kind_installed):
        """Test that ToolNotInstalledError is raised when kind is not installed."""
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_kind_installed.return_value = False
        callback = SilentCallback()

        with pytest.raises(ToolNotInstalledError, match="kind is not installed"):
            await create_kind_cluster_with_options(
                "kind", "test-cluster", "", False, None, callback
            )

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.create_minikube_cluster")
    async def test_create_minikube_cluster_with_options_success(self, mock_create_minikube, mock_minikube_installed):
        """Test creating a Minikube cluster with the new function structure."""
        mock_minikube_installed.return_value = True
        mock_create_minikube.return_value = True
        callback = SilentCallback()

        await create_minikube_cluster_with_options(
            "minikube", "test-cluster", "--memory=4096", False, None, callback
        )

        mock_create_minikube.assert_called_once()


class TestIPDetection:
    """Test IP address detection functions."""

    # Note: test_get_ip_generic_minikube and test_get_ip_generic_fallback removed
    # as these functions are now tested in the jumpstarter-kubernetes library


class TestCLICommands:
    """Test CLI command execution."""

    def test_install_command_help(self):
        runner = CliRunner()
        result = runner.invoke(install, ["--help"])
        assert result.exit_code == 0
        assert "Install the Jumpstarter service" in result.output

    def test_uninstall_command_help(self):
        runner = CliRunner()
        result = runner.invoke(uninstall, ["--help"])
        assert result.exit_code == 0
        assert "Uninstall the Jumpstarter service" in result.output

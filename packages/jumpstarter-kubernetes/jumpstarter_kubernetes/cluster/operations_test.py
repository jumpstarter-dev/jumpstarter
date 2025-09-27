"""Tests for high-level cluster operations."""

from unittest.mock import call, patch

import click
import pytest

from jumpstarter_kubernetes.cluster.operations import (
    create_cluster_and_install,
    create_cluster_only,
    create_kind_cluster_wrapper,
    create_minikube_cluster_wrapper,
    delete_cluster_by_name,
    delete_kind_cluster_wrapper,
    delete_minikube_cluster_wrapper,
    inject_certs_in_kind,
    prepare_minikube_certs,
)


class TestInjectCertsInKind:
    """Test certificate injection for Kind clusters."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.detection.detect_kind_provider")
    @patch("jumpstarter_kubernetes.cluster.operations.run_command_with_output")
    @patch("os.path.exists")
    async def test_inject_certs_in_kind_success(self, mock_exists, mock_run_command, mock_detect_provider, mock_echo):
        mock_exists.return_value = True
        mock_detect_provider.return_value = ("docker", "test-cluster-control-plane")
        mock_run_command.return_value = 0

        await inject_certs_in_kind("/path/to/certs.pem", "test-cluster")

        mock_detect_provider.assert_called_once_with("test-cluster")
        assert mock_run_command.call_count == 2  # copy and restart commands
        expected_calls = [
            call("Injecting certificates from /path/to/certs.pem into Kind cluster..."),
            call("Successfully injected custom certificates into Kind cluster")
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("os.path.exists")
    async def test_inject_certs_in_kind_file_not_found(self, mock_exists):
        mock_exists.return_value = False

        with pytest.raises(click.ClickException, match="Extra certificates file not found"):
            await inject_certs_in_kind("/nonexistent/certs.pem", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.detection.detect_kind_provider")
    @patch("jumpstarter_kubernetes.cluster.operations.run_command_with_output")
    @patch("os.path.exists")
    async def test_inject_certs_in_kind_copy_failure(
        self, mock_exists, mock_run_command, mock_detect_provider, mock_echo
    ):
        mock_exists.return_value = True
        mock_detect_provider.return_value = ("docker", "test-cluster-control-plane")
        mock_run_command.return_value = 1

        with pytest.raises(click.ClickException, match="Failed to copy certificates"):
            await inject_certs_in_kind("/path/to/certs.pem", "test-cluster")

        # Should still call the initial echo
        mock_echo.assert_called_once_with(
            "Injecting certificates from /path/to/certs.pem into Kind cluster..."
        )


class TestPrepareMinikubeCerts:
    """Test certificate preparation for Minikube clusters."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.Path.mkdir")
    @patch("shutil.copy2")
    @patch("os.path.exists")
    async def test_prepare_minikube_certs_success(self, mock_exists, mock_copy, mock_mkdir, mock_echo):
        mock_exists.side_effect = [True, False]  # cert file exists, ca.crt doesn't exist

        await prepare_minikube_certs("/path/to/certs.pem")

        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_copy.assert_called_once()
        # Check echo was called with the cert destination path
        mock_echo.assert_called_once()
        args = mock_echo.call_args[0][0]
        assert "Prepared custom certificates for Minikube:" in args

    @pytest.mark.asyncio
    @patch("os.path.exists")
    async def test_prepare_minikube_certs_file_not_found(self, mock_exists):
        mock_exists.return_value = False

        with pytest.raises(click.ClickException, match="Extra certificates file not found"):
            await prepare_minikube_certs("/nonexistent/certs.pem")


class TestCreateKindClusterWrapper:
    """Test Kind cluster creation wrapper."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster")
    @patch("jumpstarter_kubernetes.cluster.operations.inject_certs_in_kind")
    async def test_create_kind_cluster_wrapper_success(self, mock_inject_certs, mock_create, mock_installed, mock_echo):
        mock_installed.return_value = True
        mock_create.return_value = True

        await create_kind_cluster_wrapper(
            "kind", "test-cluster", "", False, "/path/to/certs.pem"
        )
        mock_create.assert_called_once_with("kind", "test-cluster", [], False)
        mock_inject_certs.assert_called_once_with("/path/to/certs.pem", "test-cluster")
        # Verify echo was called with expected messages
        expected_calls = [
            call('Creating Kind cluster "test-cluster"...'),
            call('Successfully created Kind cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    async def test_create_kind_cluster_wrapper_not_installed(self, mock_installed):
        mock_installed.return_value = False

        with pytest.raises(click.ClickException, match="kind is not installed"):
            await create_kind_cluster_wrapper("kind", "test-cluster", "", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster")
    async def test_create_kind_cluster_wrapper_no_certs(self, mock_create, mock_installed, mock_echo):
        mock_installed.return_value = True
        mock_create.return_value = True

        await create_kind_cluster_wrapper("kind", "test-cluster", "", False)
        mock_create.assert_called_once_with("kind", "test-cluster", [], False)
        # Verify echo was called with expected messages
        expected_calls = [
            call('Creating Kind cluster "test-cluster"...'),
            call('Successfully created Kind cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)


class TestCreateMinikubeClusterWrapper:
    """Test Minikube cluster creation wrapper."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_minikube_cluster")
    @patch("jumpstarter_kubernetes.cluster.operations.prepare_minikube_certs")
    async def test_create_minikube_cluster_wrapper_success(
        self, mock_prepare_certs, mock_create, mock_installed, mock_echo
    ):
        mock_installed.return_value = True
        mock_create.return_value = True

        await create_minikube_cluster_wrapper(
            "minikube", "test-cluster", "", False, "/path/to/certs.pem"
        )
        mock_prepare_certs.assert_called_once_with("/path/to/certs.pem")
        mock_create.assert_called_once_with("minikube", "test-cluster", ["--embed-certs"], False)
        # Verify echo was called with expected messages
        expected_calls = [
            call('Creating Minikube cluster "test-cluster"...'),
            call('Successfully created Minikube cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.minikube_installed")
    async def test_create_minikube_cluster_wrapper_not_installed(self, mock_installed):
        mock_installed.return_value = False

        with pytest.raises(click.ClickException, match="minikube is not installed"):
            await create_minikube_cluster_wrapper("minikube", "test-cluster", "", False)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_minikube_cluster")
    async def test_create_minikube_cluster_wrapper_no_certs(self, mock_create, mock_installed, mock_echo):
        mock_installed.return_value = True
        mock_create.return_value = True

        await create_minikube_cluster_wrapper("minikube", "test-cluster", "", False)
        mock_create.assert_called_once_with("minikube", "test-cluster", [], False)
        # Verify echo was called with expected messages
        expected_calls = [
            call('Creating Minikube cluster "test-cluster"...'),
            call('Successfully created Minikube cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)


class TestDeleteClusterWrappers:
    """Test cluster deletion wrappers."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_kind_cluster")
    async def test_delete_kind_cluster_wrapper(self, mock_delete, mock_installed, mock_echo):
        mock_installed.return_value = True
        mock_delete.return_value = True

        await delete_kind_cluster_wrapper("kind", "test-cluster")

        mock_delete.assert_called_once_with("kind", "test-cluster")
        # Verify echo was called with expected messages
        expected_calls = [
            call('Deleting Kind cluster "test-cluster"...'),
            call('Successfully deleted Kind cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_minikube_cluster")
    async def test_delete_minikube_cluster_wrapper(self, mock_delete, mock_installed, mock_echo):
        mock_installed.return_value = True
        mock_delete.return_value = True

        await delete_minikube_cluster_wrapper("minikube", "test-cluster")

        mock_delete.assert_called_once_with("minikube", "test-cluster")
        # Verify echo was called with expected messages
        expected_calls = [
            call('Deleting Minikube cluster "test-cluster"...'),
            call('Successfully deleted Minikube cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)


class TestDeleteClusterByName:
    """Test cluster deletion by name."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_kind_cluster_wrapper")
    async def test_delete_cluster_by_name_kind(self, mock_delete_kind, mock_detect, mock_echo):
        mock_detect.return_value = "kind"
        mock_delete_kind.return_value = None

        await delete_cluster_by_name("test-cluster", force=True)

        mock_detect.assert_called_once_with("test-cluster")
        mock_delete_kind.assert_called_once_with("kind", "test-cluster")
        expected_calls = [
            call('Auto-detected kind cluster "test-cluster"'),
            call('Successfully deleted kind cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_minikube_cluster_wrapper")
    async def test_delete_cluster_by_name_minikube(self, mock_delete_minikube, mock_detect, mock_echo):
        mock_detect.return_value = "minikube"
        mock_delete_minikube.return_value = None

        await delete_cluster_by_name("test-cluster", force=True)

        mock_detect.assert_called_once_with("test-cluster")
        mock_delete_minikube.assert_called_once_with("minikube", "test-cluster")
        expected_calls = [
            call('Auto-detected minikube cluster "test-cluster"'),
            call('Successfully deleted minikube cluster "test-cluster"')
        ]
        mock_echo.assert_has_calls(expected_calls)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    async def test_delete_cluster_by_name_not_found(self, mock_detect):
        mock_detect.return_value = None

        with pytest.raises(click.ClickException, match='No cluster named "test-cluster" found'):
            await delete_cluster_by_name("test-cluster", force=True)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.click.echo")
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_kind_cluster_wrapper")
    async def test_delete_cluster_by_name_with_type(
        self, mock_delete_kind, mock_cluster_exists, mock_installed, mock_detect, mock_echo
    ):
        mock_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_delete_kind.return_value = None

        await delete_cluster_by_name("test-cluster", cluster_type="kind", force=True)

        mock_detect.assert_not_called()
        mock_installed.assert_called_once_with("kind")
        mock_cluster_exists.assert_called_once_with("kind", "test-cluster")
        mock_delete_kind.assert_called_once_with("kind", "test-cluster")
        # No auto-detection echo, just success echo
        mock_echo.assert_called_once_with('Successfully deleted kind cluster "test-cluster"')


class TestCreateClusterOnly:
    """Test cluster-only creation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.create_cluster_and_install")
    async def test_create_cluster_only_kind(self, mock_create_and_install):
        mock_create_and_install.return_value = None

        await create_cluster_only("kind", False, "test-cluster", "", "", "kind", "minikube")

        mock_create_and_install.assert_called_once_with(
            "kind", False, "test-cluster", "", "", "kind", "minikube", None, install_jumpstarter=False
        )

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.create_cluster_and_install")
    async def test_create_cluster_only_minikube(self, mock_create_and_install):
        mock_create_and_install.return_value = None

        await create_cluster_only("minikube", False, "test-cluster", "", "", "kind", "minikube")

        mock_create_and_install.assert_called_once_with(
            "minikube", False, "test-cluster", "", "", "kind", "minikube", None, install_jumpstarter=False
        )

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.create_cluster_and_install")
    async def test_create_cluster_only_invalid_name(self, mock_create_and_install):
        mock_create_and_install.side_effect = click.ClickException("Invalid cluster name")

        with pytest.raises(click.ClickException, match="Invalid cluster name"):
            await create_cluster_only("kind", False, "invalid-cluster", "", "", "kind", "minikube")


class TestCreateClusterAndInstall:
    """Test cluster creation with installation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.helm_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_wrapper")
    @patch("jumpstarter_kubernetes.cluster.operations.configure_endpoints")
    @patch("jumpstarter_kubernetes.cluster.operations.install_jumpstarter_helm_chart")
    async def test_create_cluster_and_install_success(
        self, mock_install, mock_configure, mock_create, mock_helm_installed
    ):
        mock_helm_installed.return_value = True
        mock_configure.return_value = ("192.168.1.100", "test.domain", "grpc.test:8082", "router.test:8083")

        await create_cluster_and_install("kind", False, "test-cluster", "", "", "kind", "minikube", version="1.0.0")

        mock_create.assert_called_once()
        mock_configure.assert_called_once()
        mock_install.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.helm_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_wrapper")
    async def test_create_cluster_and_install_no_helm(self, mock_create_wrapper, mock_helm_installed):
        mock_create_wrapper.return_value = None
        mock_helm_installed.return_value = False

        with pytest.raises(click.ClickException, match="helm is not installed \\(or not in your PATH\\)"):
            await create_cluster_and_install("kind", False, "test-cluster", "", "", "kind", "minikube")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.helm_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_wrapper")
    @patch("jumpstarter_kubernetes.cluster.operations.configure_endpoints")
    async def test_create_cluster_and_install_no_version(
        self, mock_configure, mock_create, mock_helm_installed
    ):
        mock_create.return_value = None
        mock_helm_installed.return_value = True
        mock_configure.return_value = ("192.168.1.100", "test.domain", "grpc.test:8082", "router.test:8083")

        with pytest.raises(click.ClickException, match="Version must be specified when installing Jumpstarter"):
            await create_cluster_and_install("kind", False, "test-cluster", "", "", "kind", "minikube")

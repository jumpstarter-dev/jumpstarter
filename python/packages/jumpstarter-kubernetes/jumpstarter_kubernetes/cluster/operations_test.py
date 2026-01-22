"""Tests for high-level cluster operations."""

from unittest.mock import ANY, patch

import pytest

from jumpstarter_kubernetes.cluster.operations import (
    create_cluster_and_install,
    create_cluster_only,
    delete_cluster_by_name,
)
from jumpstarter_kubernetes.exceptions import ClusterNotFoundError, ClusterTypeValidationError


class TestDeleteClusterByName:
    """Test cluster deletion by name."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_kind_cluster_with_feedback")
    async def test_delete_cluster_by_name_kind(self, mock_delete_kind, mock_detect):
        mock_detect.return_value = "kind"
        mock_delete_kind.return_value = None

        await delete_cluster_by_name("test-cluster", force=True)

        mock_detect.assert_called_once_with("test-cluster")
        mock_delete_kind.assert_called_once_with("kind", "test-cluster", ANY)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_minikube_cluster_with_feedback")
    async def test_delete_cluster_by_name_minikube(self, mock_delete_minikube, mock_detect):
        mock_detect.return_value = "minikube"
        mock_delete_minikube.return_value = None

        await delete_cluster_by_name("test-cluster", force=True)

        mock_detect.assert_called_once_with("test-cluster")
        mock_delete_minikube.assert_called_once_with("minikube", "test-cluster", ANY)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    async def test_delete_cluster_by_name_not_found(self, mock_detect):
        mock_detect.return_value = None

        with pytest.raises(ClusterNotFoundError):
            await delete_cluster_by_name("test-cluster", force=True)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.operations.delete_kind_cluster_with_feedback")
    async def test_delete_cluster_by_name_with_type(
        self, mock_delete_kind, mock_cluster_exists, mock_installed, mock_detect
    ):
        mock_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_delete_kind.return_value = None

        await delete_cluster_by_name("test-cluster", cluster_type="kind", force=True)

        mock_detect.assert_not_called()
        mock_installed.assert_called_once_with("kind")
        mock_cluster_exists.assert_called_once_with("kind", "test-cluster")
        mock_delete_kind.assert_called_once_with("kind", "test-cluster", ANY)

    @pytest.mark.asyncio
    async def test_delete_cluster_unsupported_type_explicit(self):
        """Test that explicitly specifying an unsupported cluster type raises ClusterTypeValidationError."""
        with pytest.raises(ClusterTypeValidationError) as exc_info:
            await delete_cluster_by_name("test-cluster", cluster_type="remote", force=True)

        assert "remote" in str(exc_info.value)
        assert "kind" in str(exc_info.value)
        assert "minikube" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.detect_existing_cluster_type")
    async def test_delete_cluster_unsupported_type_auto_detected(self, mock_detect):
        """Test that auto-detecting an unsupported cluster type raises ClusterTypeValidationError."""
        mock_detect.return_value = "remote"

        with pytest.raises(ClusterTypeValidationError) as exc_info:
            await delete_cluster_by_name("test-cluster", force=True)

        assert "remote" in str(exc_info.value)
        assert "kind" in str(exc_info.value)
        assert "minikube" in str(exc_info.value)


class TestCreateClusterOnly:
    """Test cluster-only creation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.create_cluster_and_install")
    async def test_create_cluster_only_kind(self, mock_create_and_install):
        mock_create_and_install.return_value = None

        await create_cluster_only("kind", False, "test-cluster", "", "", "kind", "minikube")

        mock_create_and_install.assert_called_once_with(
            "kind", False, "test-cluster", "", "", "kind", "minikube", None, install_jumpstarter=False, callback=None
        )

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.create_cluster_and_install")
    async def test_create_cluster_only_minikube(self, mock_create_and_install):
        mock_create_and_install.return_value = None

        await create_cluster_only("minikube", False, "test-cluster", "", "", "kind", "minikube")

        mock_create_and_install.assert_called_once_with(
            "minikube",
            False,
            "test-cluster",
            "",
            "",
            "kind",
            "minikube",
            None,
            install_jumpstarter=False,
            callback=None,
        )


class TestCreateClusterAndInstall:
    """Test cluster creation with installation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.helm_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_with_options")
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
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_with_options")
    async def test_create_cluster_and_install_no_helm(self, mock_create_wrapper, mock_helm_installed):
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_create_wrapper.return_value = None
        mock_helm_installed.return_value = False

        with pytest.raises(ToolNotInstalledError):
            await create_cluster_and_install("kind", False, "test-cluster", "", "", "kind", "minikube")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.operations.helm_installed")
    @patch("jumpstarter_kubernetes.cluster.operations.create_kind_cluster_with_options")
    @patch("jumpstarter_kubernetes.cluster.operations.configure_endpoints")
    async def test_create_cluster_and_install_no_version(self, mock_configure, mock_create, mock_helm_installed):
        from jumpstarter_kubernetes.exceptions import ClusterOperationError

        mock_create.return_value = None
        mock_helm_installed.return_value = True
        mock_configure.return_value = ("192.168.1.100", "test.domain", "grpc.test:8082", "router.test:8083")

        with pytest.raises(ClusterOperationError):
            await create_cluster_and_install("kind", False, "test-cluster", "", "", "kind", "minikube")

    @pytest.mark.asyncio
    async def test_create_cluster_and_install_unsupported_cluster_type(self):
        """Test that creating a cluster with an unsupported cluster type raises ClusterTypeValidationError."""
        with pytest.raises(ClusterTypeValidationError) as exc_info:
            await create_cluster_and_install("remote", False, "test-cluster", "", "", "kind", "minikube")

        assert "Unsupported cluster_type: remote" in str(exc_info.value)

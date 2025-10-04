"""Tests for Kind cluster management operations."""

from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.kind import (
    create_kind_cluster,
    delete_kind_cluster,
    kind_cluster_exists,
    kind_installed,
)
from jumpstarter_kubernetes.exceptions import ClusterAlreadyExistsError


class TestKindInstalled:
    """Test Kind installation detection."""

    @patch("jumpstarter_kubernetes.cluster.kind.shutil.which")
    def test_kind_installed_true(self, mock_which):
        mock_which.return_value = "/usr/local/bin/kind"
        assert kind_installed("kind") is True
        mock_which.assert_called_once_with("kind")

    @patch("jumpstarter_kubernetes.cluster.kind.shutil.which")
    def test_kind_installed_false(self, mock_which):
        mock_which.return_value = None
        assert kind_installed("kind") is False
        mock_which.assert_called_once_with("kind")

    @patch("jumpstarter_kubernetes.cluster.kind.shutil.which")
    def test_kind_installed_custom_binary(self, mock_which):
        mock_which.return_value = "/custom/path/kind"
        assert kind_installed("custom-kind") is True
        mock_which.assert_called_once_with("custom-kind")


class TestKindClusterExists:
    """Test Kind cluster existence checking."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command")
    async def test_kind_cluster_exists_true(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.return_value = (0, "", "")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["kind", "get", "kubeconfig", "--name", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command")
    async def test_kind_cluster_exists_false(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.return_value = (1, "", "cluster not found")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    async def test_kind_cluster_exists_kind_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command")
    async def test_kind_cluster_exists_runtime_error(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.side_effect = RuntimeError("Command failed")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command")
    async def test_kind_cluster_exists_custom_binary(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.return_value = (0, "", "")

        result = await kind_cluster_exists("custom-kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["custom-kind", "get", "kubeconfig", "--name", "test-cluster"])


class TestCreateKindCluster:
    """Test Kind cluster creation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_create_kind_cluster_success(self, mock_run_command, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_kind_cluster("kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once()
        args = mock_run_command.call_args[0][0]
        assert args[0] == "kind"
        assert args[1] == "create"
        assert args[2] == "cluster"
        assert "--name" in args
        assert "test-cluster" in args

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    async def test_create_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(RuntimeError, match="kind is not installed"):
            await create_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    async def test_create_kind_cluster_already_exists(self, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True

        with pytest.raises(ClusterAlreadyExistsError) as exc_info:
            await create_kind_cluster("kind", "test-cluster")

        assert exc_info.value.cluster_name == "test-cluster"
        assert exc_info.value.cluster_type == "kind"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.delete_kind_cluster")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_create_kind_cluster_force_recreate(
        self, mock_run_command, mock_delete, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_delete.return_value = True
        mock_run_command.return_value = 0

        result = await create_kind_cluster("kind", "test-cluster", force_recreate=True)

        assert result is True
        mock_delete.assert_called_once_with("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_create_kind_cluster_with_extra_args(
        self, mock_run_command, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_kind_cluster("kind", "test-cluster", extra_args=["--verbosity=1"])

        assert result is True
        args = mock_run_command.call_args[0][0]
        assert "--verbosity=1" in args


    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_create_kind_cluster_command_failure(
        self, mock_run_command, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 1

        with pytest.raises(RuntimeError, match="Failed to create Kind cluster 'test-cluster'"):
            await create_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_create_kind_cluster_custom_binary(
        self, mock_run_command, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_kind_cluster("custom-kind", "test-cluster")

        assert result is True
        args = mock_run_command.call_args[0][0]
        assert args[0] == "custom-kind"


class TestDeleteKindCluster:
    """Test Kind cluster deletion."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_delete_kind_cluster_success(self, mock_run_command, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 0

        result = await delete_kind_cluster("kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["kind", "delete", "cluster", "--name", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    async def test_delete_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(RuntimeError, match="kind is not installed"):
            await delete_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    async def test_delete_kind_cluster_already_deleted(self, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False

        result = await delete_kind_cluster("kind", "test-cluster")

        assert result is True  # Already deleted, consider successful

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_delete_kind_cluster_command_failure(
        self, mock_run_command, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 1

        with pytest.raises(RuntimeError, match="Failed to delete Kind cluster 'test-cluster'"):
            await delete_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.kind.run_command_with_output")
    async def test_delete_kind_cluster_custom_binary(
        self, mock_run_command, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 0

        result = await delete_kind_cluster("custom-kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["custom-kind", "delete", "cluster", "--name", "test-cluster"])

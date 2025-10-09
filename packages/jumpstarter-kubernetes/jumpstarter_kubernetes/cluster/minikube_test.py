"""Tests for Minikube cluster management operations."""

from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.minikube import (
    create_minikube_cluster,
    delete_minikube_cluster,
    minikube_cluster_exists,
    minikube_installed,
)


class TestMinikubeInstalled:
    """Test Minikube installation detection."""

    @patch("jumpstarter_kubernetes.cluster.minikube.shutil.which")
    def test_minikube_installed_true(self, mock_which):
        mock_which.return_value = "/usr/local/bin/minikube"
        assert minikube_installed("minikube") is True
        mock_which.assert_called_once_with("minikube")

    @patch("jumpstarter_kubernetes.cluster.minikube.shutil.which")
    def test_minikube_installed_false(self, mock_which):
        mock_which.return_value = None
        assert minikube_installed("minikube") is False
        mock_which.assert_called_once_with("minikube")

    @patch("jumpstarter_kubernetes.cluster.minikube.shutil.which")
    def test_minikube_installed_custom_binary(self, mock_which):
        mock_which.return_value = "/custom/path/minikube"
        assert minikube_installed("custom-minikube") is True
        mock_which.assert_called_once_with("custom-minikube")


class TestMinikubeClusterExists:
    """Test Minikube cluster existence checking."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command")
    async def test_minikube_cluster_exists_true(self, mock_run_command, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        # Mock profile list to return a valid profile
        mock_run_command.return_value = (
            0,
            '{"valid": [{"Name": "test-cluster", "Status": "Running"}], "invalid": []}',
            ""
        )

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is True
        # Should call profile list first
        mock_run_command.assert_called_with(["minikube", "profile", "list", "-o", "json"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command")
    async def test_minikube_cluster_exists_false(self, mock_run_command, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_run_command.return_value = (1, "", "profile not found")

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    async def test_minikube_cluster_exists_minikube_not_installed(self, mock_minikube_installed):
        mock_minikube_installed.return_value = False

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command")
    async def test_minikube_cluster_exists_runtime_error(self, mock_run_command, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        # RuntimeError with "profile not found" should return False
        mock_run_command.side_effect = RuntimeError("profile not found")

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command")
    async def test_minikube_cluster_exists_stopped_cluster(self, mock_run_command, mock_minikube_installed):
        """Test that a stopped cluster is detected as existing"""
        mock_minikube_installed.return_value = True
        # Mock profile list to show stopped cluster
        mock_run_command.return_value = (
            0,
            '{"valid": [{"Name": "test-cluster", "Status": "Stopped"}], "invalid": []}',
            ""
        )

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is True

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command")
    async def test_minikube_cluster_exists_custom_binary(self, mock_run_command, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        # Mock profile list to return a valid profile
        mock_run_command.return_value = (
            0,
            '{"valid": [{"Name": "test-cluster", "Status": "Running"}], "invalid": []}',
            ""
        )

        result = await minikube_cluster_exists("custom-minikube", "test-cluster")

        assert result is True
        # Should call profile list first
        mock_run_command.assert_called_with(["custom-minikube", "profile", "list", "-o", "json"])




class TestCreateMinikubeCluster:
    """Test Minikube cluster creation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_create_minikube_cluster_success(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_minikube_cluster("minikube", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once()
        args = mock_run_command.call_args[0][0]
        assert args[0] == "minikube"
        assert args[1] == "start"
        assert "--profile" in args
        assert "test-cluster" in args
        assert "--extra-config=apiserver.service-node-port-range=30000-32767" in args

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    async def test_create_minikube_cluster_not_installed(self, mock_minikube_installed):
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_minikube_installed.return_value = False

        with pytest.raises(ToolNotInstalledError):
            await create_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    async def test_create_minikube_cluster_already_exists(self, mock_cluster_exists, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True

        # Should return True without raising error when cluster exists and force_recreate=False
        result = await create_minikube_cluster("minikube", "test-cluster")
        assert result is True

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_create_minikube_cluster_with_extra_args(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_minikube_cluster("minikube", "test-cluster", extra_args=["--memory=4096"])

        assert result is True
        args = mock_run_command.call_args[0][0]
        assert "--memory=4096" in args

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_create_minikube_cluster_command_failure(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        from jumpstarter_kubernetes.exceptions import ClusterOperationError

        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 1

        with pytest.raises(ClusterOperationError):
            await create_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_create_minikube_cluster_custom_binary(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False
        mock_run_command.return_value = 0

        result = await create_minikube_cluster("custom-minikube", "test-cluster")

        assert result is True
        args = mock_run_command.call_args[0][0]
        assert args[0] == "custom-minikube"


class TestDeleteMinikubeCluster:
    """Test Minikube cluster deletion."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_delete_minikube_cluster_success(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 0

        result = await delete_minikube_cluster("minikube", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["minikube", "delete", "-p", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    async def test_delete_minikube_cluster_not_installed(self, mock_minikube_installed):
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_minikube_installed.return_value = False

        with pytest.raises(ToolNotInstalledError):
            await delete_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    async def test_delete_minikube_cluster_already_deleted(self, mock_cluster_exists, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False

        result = await delete_minikube_cluster("minikube", "test-cluster")

        assert result is True  # Already deleted, consider successful

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_delete_minikube_cluster_failure(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        from jumpstarter_kubernetes.exceptions import ClusterOperationError

        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 1

        with pytest.raises(ClusterOperationError):
            await delete_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.minikube.run_command_with_output")
    async def test_delete_minikube_cluster_custom_binary(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 0

        result = await delete_minikube_cluster("custom-minikube", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["custom-minikube", "delete", "-p", "test-cluster"])

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter_kubernetes.cluster import (
    create_kind_cluster,
    create_minikube_cluster,
    delete_kind_cluster,
    delete_minikube_cluster,
    kind_cluster_exists,
    kind_installed,
    minikube_cluster_exists,
    minikube_installed,
    run_command,
    run_command_with_output,
)


class TestClusterDetection:
    """Test cluster tool detection functions."""

    @patch("jumpstarter_kubernetes.cluster.shutil.which")
    def test_kind_installed_true(self, mock_which):
        mock_which.return_value = "/usr/local/bin/kind"
        assert kind_installed("kind") is True
        mock_which.assert_called_once_with("kind")

    @patch("jumpstarter_kubernetes.cluster.shutil.which")
    def test_kind_installed_false(self, mock_which):
        mock_which.return_value = None
        assert kind_installed("kind") is False
        mock_which.assert_called_once_with("kind")

    @patch("jumpstarter_kubernetes.cluster.shutil.which")
    def test_minikube_installed_true(self, mock_which):
        mock_which.return_value = "/usr/local/bin/minikube"
        assert minikube_installed("minikube") is True
        mock_which.assert_called_once_with("minikube")

    @patch("jumpstarter_kubernetes.cluster.shutil.which")
    def test_minikube_installed_false(self, mock_which):
        mock_which.return_value = None
        assert minikube_installed("minikube") is False
        mock_which.assert_called_once_with("minikube")


class TestCommandExecution:
    """Test command execution utilities."""

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"output\n", b"")
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            returncode, stdout, stderr = await run_command(["echo", "test"])

            assert returncode == 0
            assert stdout == "output"
            assert stderr == ""
            mock_subprocess.assert_called_once_with(
                "echo", "test", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

    @pytest.mark.asyncio
    async def test_run_command_failure(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"error message\n")
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            returncode, stdout, stderr = await run_command(["false"])

            assert returncode == 1
            assert stdout == ""
            assert stderr == "error message"

    @pytest.mark.asyncio
    async def test_run_command_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("command not found")):
            with pytest.raises(RuntimeError, match="Command not found: nonexistent"):
                await run_command(["nonexistent"])

    @pytest.mark.asyncio
    async def test_run_command_with_output_success(self):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.wait.return_value = 0
            mock_subprocess.return_value = mock_process

            returncode = await run_command_with_output(["echo", "test"])

            assert returncode == 0
            mock_subprocess.assert_called_once_with("echo", "test")

    @pytest.mark.asyncio
    async def test_run_command_with_output_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("command not found")):
            with pytest.raises(RuntimeError, match="Command not found: nonexistent"):
                await run_command_with_output(["nonexistent"])


class TestClusterExistence:
    """Test cluster existence checking functions."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.run_command")
    async def test_kind_cluster_exists_true(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.return_value = (0, "", "")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["kind", "get", "kubeconfig", "--name", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.run_command")
    async def test_kind_cluster_exists_false(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.return_value = (1, "", "cluster not found")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    async def test_kind_cluster_exists_kind_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.run_command")
    async def test_kind_cluster_exists_runtime_error(self, mock_run_command, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_run_command.side_effect = RuntimeError("Command failed")

        result = await kind_cluster_exists("kind", "test-cluster")

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.run_command")
    async def test_minikube_cluster_exists_true(self, mock_run_command, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_run_command.return_value = (0, "", "")

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["minikube", "status", "-p", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    async def test_minikube_cluster_exists_minikube_not_installed(self, mock_minikube_installed):
        mock_minikube_installed.return_value = False

        result = await minikube_cluster_exists("minikube", "test-cluster")

        assert result is False


class TestKindClusterOperations:
    """Test Kind cluster creation and deletion."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    @patch("asyncio.create_subprocess_exec")
    async def test_create_kind_cluster_success(self, mock_subprocess, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b"", b"")
        mock_subprocess.return_value = mock_process

        result = await create_kind_cluster("kind", "test-cluster")

        assert result is True
        mock_subprocess.assert_called_once()
        args, kwargs = mock_subprocess.call_args
        assert args[0] == "kind"
        assert args[1] == "create"
        assert args[2] == "cluster"
        assert "--name" in args
        assert "test-cluster" in args
        assert kwargs["stdin"] == asyncio.subprocess.PIPE

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    async def test_create_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(RuntimeError, match="kind is not installed"):
            await create_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    async def test_create_kind_cluster_already_exists(self, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True

        with pytest.raises(RuntimeError, match="Kind cluster 'test-cluster' already exists"):
            await create_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.delete_kind_cluster")
    @patch("asyncio.create_subprocess_exec")
    async def test_create_kind_cluster_force_recreate(
        self, mock_subprocess, mock_delete, mock_cluster_exists, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_delete.return_value = True

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b"", b"")
        mock_subprocess.return_value = mock_process

        result = await create_kind_cluster("kind", "test-cluster", force_recreate=True)

        assert result is True
        mock_delete.assert_called_once_with("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    @patch("asyncio.create_subprocess_exec")
    async def test_create_kind_cluster_with_extra_args(self, mock_subprocess, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = (b"", b"")
        mock_subprocess.return_value = mock_process

        result = await create_kind_cluster("kind", "test-cluster", extra_args=["--verbosity=1"])

        assert result is True
        args, _ = mock_subprocess.call_args
        assert "--verbosity=1" in args

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.run_command_with_output")
    async def test_delete_kind_cluster_success(self, mock_run_command, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 0

        result = await delete_kind_cluster("kind", "test-cluster")

        assert result is True
        mock_run_command.assert_called_once_with(["kind", "delete", "cluster", "--name", "test-cluster"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    async def test_delete_kind_cluster_not_installed(self, mock_kind_installed):
        mock_kind_installed.return_value = False

        with pytest.raises(RuntimeError, match="kind is not installed"):
            await delete_kind_cluster("kind", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.kind_cluster_exists")
    async def test_delete_kind_cluster_already_deleted(self, mock_cluster_exists, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_cluster_exists.return_value = False

        result = await delete_kind_cluster("kind", "test-cluster")

        assert result is True  # Already deleted, consider successful


class TestMinikubeClusterOperations:
    """Test Minikube cluster creation and deletion."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.run_command_with_output")
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
        assert "--extra-config=apiserver.service-node-port-range=8000-9000" in args

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    async def test_create_minikube_cluster_not_installed(self, mock_minikube_installed):
        mock_minikube_installed.return_value = False

        with pytest.raises(RuntimeError, match="minikube is not installed"):
            await create_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    async def test_create_minikube_cluster_already_exists(self, mock_cluster_exists, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True

        with pytest.raises(RuntimeError, match="Minikube cluster 'test-cluster' already exists"):
            await create_minikube_cluster("minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.run_command_with_output")
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
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.run_command_with_output")
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
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    async def test_delete_minikube_cluster_already_deleted(self, mock_cluster_exists, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = False

        result = await delete_minikube_cluster("minikube", "test-cluster")

        assert result is True  # Already deleted, consider successful

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.minikube_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.run_command_with_output")
    async def test_delete_minikube_cluster_failure(
        self, mock_run_command, mock_cluster_exists, mock_minikube_installed
    ):
        mock_minikube_installed.return_value = True
        mock_cluster_exists.return_value = True
        mock_run_command.return_value = 1

        with pytest.raises(RuntimeError, match="Failed to delete Minikube cluster 'test-cluster'"):
            await delete_minikube_cluster("minikube", "test-cluster")

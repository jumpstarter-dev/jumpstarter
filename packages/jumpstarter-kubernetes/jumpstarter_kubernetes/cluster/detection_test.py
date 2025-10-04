"""Tests for cluster detection and type identification."""

from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.detection import (
    auto_detect_cluster_type,
    detect_cluster_type,
    detect_container_runtime,
    detect_existing_cluster_type,
    detect_kind_provider,
)


class TestDetectContainerRuntime:
    """Test container runtime detection."""

    @patch("shutil.which")
    def test_detect_container_runtime_docker(self, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/docker" if cmd == "docker" else None
        result = detect_container_runtime()
        assert result == "docker"

    @patch("shutil.which")
    def test_detect_container_runtime_podman(self, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/podman" if cmd == "podman" else None
        result = detect_container_runtime()
        assert result == "podman"

    @patch("shutil.which")
    def test_detect_container_runtime_nerdctl(self, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/nerdctl" if cmd == "nerdctl" else None
        result = detect_container_runtime()
        assert result == "nerdctl"

    @patch("shutil.which")
    def test_detect_container_runtime_none_available(self, mock_which):
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_which.return_value = None
        with pytest.raises(
            ToolNotInstalledError,
            match="No supported container runtime found in PATH. Kind requires docker, podman, or nerdctl.",
        ):
            detect_container_runtime()

    @patch("shutil.which")
    def test_detect_container_runtime_docker_preferred(self, mock_which):
        # Docker should be preferred when multiple are available
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}" if cmd in ["docker", "podman"] else None
        result = detect_container_runtime()
        assert result == "docker"


class TestDetectKindProvider:
    """Test Kind provider detection."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.detect_container_runtime")
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_kind_provider_control_plane(self, mock_run_command, mock_detect_runtime):
        mock_detect_runtime.return_value = "docker"
        mock_run_command.return_value = (0, "", "")

        runtime, node_name = await detect_kind_provider("test-cluster")

        assert runtime == "docker"
        assert node_name == "test-cluster-control-plane"
        mock_run_command.assert_called_once_with(["docker", "inspect", "test-cluster-control-plane"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.detect_container_runtime")
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_kind_provider_kind_prefix(self, mock_run_command, mock_detect_runtime):
        mock_detect_runtime.return_value = "docker"
        # First call fails, second succeeds
        mock_run_command.side_effect = [(1, "", ""), (0, "", "")]

        runtime, node_name = await detect_kind_provider("test-cluster")

        assert runtime == "docker"
        assert node_name == "kind-test-cluster-control-plane"
        assert mock_run_command.call_count == 2

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.detect_container_runtime")
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_kind_provider_default_cluster(self, mock_run_command, mock_detect_runtime):
        mock_detect_runtime.return_value = "docker"
        mock_run_command.return_value = (0, "", "")

        runtime, node_name = await detect_kind_provider("kind")

        assert runtime == "docker"
        assert node_name == "kind-control-plane"
        mock_run_command.assert_called_once_with(["docker", "inspect", "kind-control-plane"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.detect_container_runtime")
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_kind_provider_fallback(self, mock_run_command, mock_detect_runtime):
        mock_detect_runtime.return_value = "podman"
        mock_run_command.return_value = (1, "", "")  # All checks fail

        runtime, node_name = await detect_kind_provider("test-cluster")

        assert runtime == "podman"
        assert node_name == "test-cluster-control-plane"  # Fallback

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.detect_container_runtime")
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_kind_provider_runtime_error(self, mock_run_command, mock_detect_runtime):
        mock_detect_runtime.return_value = "docker"
        mock_run_command.side_effect = RuntimeError("Command failed")

        runtime, node_name = await detect_kind_provider("test-cluster")

        assert runtime == "docker"
        assert node_name == "test-cluster-control-plane"  # Fallback


class TestDetectExistingClusterType:
    """Test detection of existing cluster types."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_cluster_exists")
    async def test_detect_existing_cluster_type_kind_only(
        self, mock_minikube_exists, mock_kind_exists, mock_minikube_installed, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = True
        mock_kind_exists.return_value = True
        mock_minikube_exists.return_value = False

        result = await detect_existing_cluster_type("test-cluster")

        assert result == "kind"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_cluster_exists")
    async def test_detect_existing_cluster_type_minikube_only(
        self, mock_minikube_exists, mock_kind_exists, mock_minikube_installed, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = True
        mock_kind_exists.return_value = False
        mock_minikube_exists.return_value = True

        result = await detect_existing_cluster_type("test-cluster")

        assert result == "minikube"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_cluster_exists")
    async def test_detect_existing_cluster_type_both_exist(
        self, mock_minikube_exists, mock_kind_exists, mock_minikube_installed, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = True
        mock_kind_exists.return_value = True
        mock_minikube_exists.return_value = True

        from jumpstarter_kubernetes.exceptions import ClusterOperationError

        with pytest.raises(
            ClusterOperationError,
            match='Both Kind and Minikube clusters named "test-cluster" exist',
        ):
            await detect_existing_cluster_type("test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.kind_cluster_exists")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_cluster_exists")
    async def test_detect_existing_cluster_type_none_exist(
        self, mock_minikube_exists, mock_kind_exists, mock_minikube_installed, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = True
        mock_kind_exists.return_value = False
        mock_minikube_exists.return_value = False

        result = await detect_existing_cluster_type("test-cluster")

        assert result is None

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    async def test_detect_existing_cluster_type_kind_not_installed(self, mock_minikube_installed, mock_kind_installed):
        mock_kind_installed.return_value = False
        mock_minikube_installed.return_value = True

        result = await detect_existing_cluster_type("test-cluster")

        assert result is None

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.kind_cluster_exists")
    async def test_detect_existing_cluster_type_runtime_error(
        self, mock_kind_exists, mock_minikube_installed, mock_kind_installed
    ):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = False
        mock_kind_exists.side_effect = RuntimeError("Command failed")

        result = await detect_existing_cluster_type("test-cluster")

        assert result is None


class TestAutoDetectClusterType:
    """Test automatic cluster type detection."""

    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    def test_auto_detect_cluster_type_kind_available(self, mock_minikube_installed, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = False

        result = auto_detect_cluster_type()

        assert result == "kind"

    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    def test_auto_detect_cluster_type_minikube_only(self, mock_minikube_installed, mock_kind_installed):
        mock_kind_installed.return_value = False
        mock_minikube_installed.return_value = True

        result = auto_detect_cluster_type()

        assert result == "minikube"

    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    def test_auto_detect_cluster_type_kind_preferred(self, mock_minikube_installed, mock_kind_installed):
        mock_kind_installed.return_value = True
        mock_minikube_installed.return_value = True

        result = auto_detect_cluster_type()

        assert result == "kind"  # Kind is preferred

    @patch("jumpstarter_kubernetes.cluster.detection.kind_installed")
    @patch("jumpstarter_kubernetes.cluster.detection.minikube_installed")
    def test_auto_detect_cluster_type_none_available(self, mock_minikube_installed, mock_kind_installed):
        mock_kind_installed.return_value = False
        mock_minikube_installed.return_value = False

        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        with pytest.raises(
            ToolNotInstalledError,
            match="Neither Kind nor Minikube is installed",
        ):
            auto_detect_cluster_type()


class TestDetectClusterType:
    """Test cluster type detection from context and server URL."""

    @pytest.mark.asyncio
    async def test_detect_cluster_type_kind_context_prefix(self):
        result = await detect_cluster_type("kind-test-cluster", "https://127.0.0.1:6443")
        assert result == "kind"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_kind_context_name(self):
        result = await detect_cluster_type("kind", "https://127.0.0.1:6443")
        assert result == "kind"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_minikube_context(self):
        result = await detect_cluster_type("minikube", "https://192.168.49.2:8443")
        assert result == "minikube"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_localhost(self):
        result = await detect_cluster_type("local-cluster", "https://localhost:6443")
        assert result == "kind"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_127_0_0_1(self):
        result = await detect_cluster_type("local-cluster", "https://127.0.0.1:6443")
        assert result == "kind"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_0_0_0_0(self):
        result = await detect_cluster_type("local-cluster", "https://0.0.0.0:6443")
        assert result == "kind"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_cluster_type_minikube_ip_range_192(self, mock_run_command):
        mock_run_command.return_value = (0, '{"valid": [{"Name": "test"}]}', "")

        result = await detect_cluster_type("test-cluster", "https://192.168.49.2:8443")

        assert result == "minikube"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_cluster_type_minikube_ip_range_172(self, mock_run_command):
        mock_run_command.return_value = (0, '{"valid": [{"Name": "test"}]}', "")

        result = await detect_cluster_type("test-cluster", "https://172.17.0.2:443")

        assert result == "minikube"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_cluster_type_minikube_ip_no_profiles(self, mock_run_command):
        mock_run_command.return_value = (1, "", "error")

        result = await detect_cluster_type("test-cluster", "https://192.168.49.2:8443")

        assert result == "remote"  # Falls back to remote if no minikube profiles

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_cluster_type_minikube_invalid_json(self, mock_run_command):
        mock_run_command.return_value = (0, "invalid json", "")

        result = await detect_cluster_type("test-cluster", "https://192.168.49.2:8443")

        assert result == "remote"  # Falls back to remote if JSON parsing fails

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.detection.run_command")
    async def test_detect_cluster_type_minikube_runtime_error(self, mock_run_command):
        mock_run_command.side_effect = RuntimeError("Command failed")

        result = await detect_cluster_type("test-cluster", "https://192.168.49.2:8443")

        assert result == "remote"  # Falls back to remote if command fails

    @pytest.mark.asyncio
    async def test_detect_cluster_type_remote(self):
        result = await detect_cluster_type("production-cluster", "https://k8s.example.com:443")
        assert result == "remote"

    @pytest.mark.asyncio
    async def test_detect_cluster_type_custom_minikube_binary(self):
        result = await detect_cluster_type("test-cluster", "https://example.com", minikube="custom-minikube")
        assert result == "remote"

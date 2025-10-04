"""Tests for Helm installation functions."""

from unittest.mock import AsyncMock, patch

import pytest

from jumpstarter_kubernetes.install import helm_installed, install_helm_chart


class TestHelmInstalled:
    """Test helm_installed function."""

    @patch("jumpstarter_kubernetes.install.shutil.which")
    def test_helm_installed_true(self, mock_which):
        mock_which.return_value = "/usr/local/bin/helm"
        assert helm_installed("helm") is True
        mock_which.assert_called_once_with("helm")

    @patch("jumpstarter_kubernetes.install.shutil.which")
    def test_helm_installed_false(self, mock_which):
        mock_which.return_value = None
        assert helm_installed("helm") is False
        mock_which.assert_called_once_with("helm")

    @patch("jumpstarter_kubernetes.install.shutil.which")
    def test_helm_installed_custom_path(self, mock_which):
        mock_which.return_value = "/custom/path/helm"
        assert helm_installed("/custom/path/helm") is True
        mock_which.assert_called_once_with("/custom/path/helm")


class TestInstallHelmChart:
    """Test install_helm_chart function."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_basic(self, mock_subprocess):
        """Test basic helm chart installation without values files."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="nodeport",
            version="1.0.0",
            kubeconfig=None,
            context=None,
            helm="helm",
            values_files=None,
        )

        # Verify the subprocess was called with correct arguments
        args = mock_subprocess.call_args[0]
        assert args[0] == "helm"
        assert args[1] == "upgrade"
        assert args[2] == "jumpstarter"
        assert "--install" in args
        assert "oci://quay.io/jumpstarter/helm" in args
        assert "--namespace" in args
        assert "jumpstarter-lab" in args
        assert "--version" in args
        assert "1.0.0" in args
        assert "--wait" in args

        # Verify no -f flags when values_files is None
        assert "-f" not in args

        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_with_single_values_file(self, mock_subprocess):
        """Test helm chart installation with a single values file."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        values_files = ["/path/to/values.yaml"]

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="nodeport",
            version="1.0.0",
            kubeconfig=None,
            context=None,
            helm="helm",
            values_files=values_files,
        )

        # Verify the subprocess was called with correct arguments including -f
        args = mock_subprocess.call_args[0]
        assert "-f" in args
        f_index = args.index("-f")
        assert args[f_index + 1] == "/path/to/values.yaml"

        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_with_multiple_values_files(self, mock_subprocess):
        """Test helm chart installation with multiple values files."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        values_files = ["/path/to/values.yaml", "/path/to/values.kind.yaml"]

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="nodeport",
            version="1.0.0",
            kubeconfig=None,
            context=None,
            helm="helm",
            values_files=values_files,
        )

        # Verify the subprocess was called with correct arguments including multiple -f flags
        args = mock_subprocess.call_args[0]

        # Find all -f flags
        f_indices = [i for i, arg in enumerate(args) if arg == "-f"]
        assert len(f_indices) == 2

        # Verify the values files are in the correct order
        assert args[f_indices[0] + 1] == "/path/to/values.yaml"
        assert args[f_indices[1] + 1] == "/path/to/values.kind.yaml"

        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_with_kubeconfig_and_context(self, mock_subprocess):
        """Test helm chart installation with kubeconfig and context."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="nodeport",
            version="1.0.0",
            kubeconfig="/path/to/kubeconfig",
            context="test-context",
            helm="helm",
            values_files=None,
        )

        # Verify the subprocess was called with kubeconfig and context
        args = mock_subprocess.call_args[0]
        assert "--kubeconfig" in args
        kubeconfig_index = args.index("--kubeconfig")
        assert args[kubeconfig_index + 1] == "/path/to/kubeconfig"

        assert "--kube-context" in args
        context_index = args.index("--kube-context")
        assert args[context_index + 1] == "test-context"

        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_with_all_options(self, mock_subprocess):
        """Test helm chart installation with all options including values files, kubeconfig, and context."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        values_files = ["/path/to/values1.yaml", "/path/to/values2.yaml", "/path/to/values3.yaml"]

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="ingress",
            version="1.0.0",
            kubeconfig="/path/to/kubeconfig",
            context="prod-context",
            helm="/usr/local/bin/helm",
            values_files=values_files,
        )

        # Verify all options are present
        args = mock_subprocess.call_args[0]

        # Check helm binary
        assert args[0] == "/usr/local/bin/helm"

        # Check kubeconfig and context
        assert "--kubeconfig" in args
        assert "/path/to/kubeconfig" in args
        assert "--kube-context" in args
        assert "prod-context" in args

        # Check values files
        f_indices = [i for i, arg in enumerate(args) if arg == "-f"]
        assert len(f_indices) == 3
        assert args[f_indices[0] + 1] == "/path/to/values1.yaml"
        assert args[f_indices[1] + 1] == "/path/to/values2.yaml"
        assert args[f_indices[2] + 1] == "/path/to/values3.yaml"

        # Check mode
        assert "jumpstarter-controller.grpc.mode=ingress" in args

        mock_process.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.install.asyncio.create_subprocess_exec")
    async def test_install_helm_chart_empty_values_files_list(self, mock_subprocess):
        """Test helm chart installation with empty values files list."""
        mock_process = AsyncMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_subprocess.return_value = mock_process

        await install_helm_chart(
            chart="oci://quay.io/jumpstarter/helm",
            name="jumpstarter",
            namespace="jumpstarter-lab",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="nodeport",
            version="1.0.0",
            kubeconfig=None,
            context=None,
            helm="helm",
            values_files=[],
        )

        # Verify no -f flags when values_files is empty list
        args = mock_subprocess.call_args[0]
        assert "-f" not in args

        mock_process.wait.assert_called_once()

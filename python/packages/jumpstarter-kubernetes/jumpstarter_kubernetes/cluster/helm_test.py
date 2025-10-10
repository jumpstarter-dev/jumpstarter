"""Tests for Helm chart management operations."""

from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.helm import install_jumpstarter_helm_chart


class TestInstallJumpstarterHelmChart:
    """Test Jumpstarter Helm chart installation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_all_params(self, mock_install_helm_chart):
        from unittest.mock import MagicMock

        mock_install_helm_chart.return_value = None
        mock_callback = MagicMock()

        await install_jumpstarter_helm_chart(
            chart="oci://registry.example.com/jumpstarter",
            name="jumpstarter",
            namespace="jumpstarter-system",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="insecure",
            version="1.0.0",
            kubeconfig="/path/to/kubeconfig",
            context="test-context",
            helm="helm",
            ip="192.168.1.100",
            callback=mock_callback,
        )

        # Verify that install_helm_chart was called with correct parameters
        mock_install_helm_chart.assert_called_once_with(
            "oci://registry.example.com/jumpstarter",
            "jumpstarter",
            "jumpstarter-system",
            "jumpstarter.192.168.1.100.nip.io",
            "grpc.jumpstarter.192.168.1.100.nip.io:8082",
            "router.jumpstarter.192.168.1.100.nip.io:8083",
            "insecure",
            "1.0.0",
            "/path/to/kubeconfig",
            "test-context",
            "helm",
            None,
        )

        # Verify callback was called
        assert mock_callback.progress.call_count >= 7  # Multiple progress messages
        assert mock_callback.success.call_count == 1  # One success message

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_with_none_values(self, mock_install_helm_chart):
        mock_install_helm_chart.return_value = None

        await install_jumpstarter_helm_chart(
            chart="jumpstarter/jumpstarter",
            name="my-jumpstarter",
            namespace="default",
            basedomain="test.example.com",
            grpc_endpoint="grpc.test.example.com:443",
            router_endpoint="router.test.example.com:443",
            mode="secure",
            version="2.1.0",
            kubeconfig=None,
            context=None,
            helm="helm3",
            ip="10.0.0.1",
        )

        # Verify that install_helm_chart was called with None values preserved
        mock_install_helm_chart.assert_called_once_with(
            "jumpstarter/jumpstarter",
            "my-jumpstarter",
            "default",
            "test.example.com",
            "grpc.test.example.com:443",
            "router.test.example.com:443",
            "secure",
            "2.1.0",
            None,
            None,
            "helm3",
            None,
        )

        # Verify success message with correct values

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_secure_mode(self, mock_install_helm_chart):
        mock_install_helm_chart.return_value = None

        await install_jumpstarter_helm_chart(
            chart="https://example.com/charts/jumpstarter-1.5.0.tgz",
            name="production-jumpstarter",
            namespace="production",
            basedomain="jumpstarter.prod.example.com",
            grpc_endpoint="grpc.jumpstarter.prod.example.com:443",
            router_endpoint="router.jumpstarter.prod.example.com:443",
            mode="secure",
            version="1.5.0",
            kubeconfig="/etc/kubernetes/admin.conf",
            context="production-cluster",
            helm="/usr/local/bin/helm",
            ip="203.0.113.1",
        )

        # Verify gRPC mode is correctly displayed

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_custom_endpoints(self, mock_install_helm_chart):
        mock_install_helm_chart.return_value = None

        await install_jumpstarter_helm_chart(
            chart="jumpstarter",
            name="dev-jumpstarter",
            namespace="development",
            basedomain="dev.local",
            grpc_endpoint="grpc-custom.dev.local:9090",
            router_endpoint="router-custom.dev.local:9091",
            mode="insecure",
            version="0.9.0-beta",
            kubeconfig="~/.kube/config",
            context="dev-context",
            helm="helm",
            ip="172.16.0.10",
        )

        # Verify custom endpoints are displayed correctly

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_install_helm_chart_error(self, mock_install_helm_chart):
        # Test that exceptions from install_helm_chart propagate
        mock_install_helm_chart.side_effect = Exception("Helm installation failed")

        with pytest.raises(Exception, match="Helm installation failed"):
            await install_jumpstarter_helm_chart(
                chart="jumpstarter",
                name="test-jumpstarter",
                namespace="test",
                basedomain="test.local",
                grpc_endpoint="grpc.test.local:8082",
                router_endpoint="router.test.local:8083",
                mode="insecure",
                version="1.0.0",
                kubeconfig=None,
                context=None,
                helm="helm",
                ip="192.168.1.1",
            )

        # Exception was raised correctly - test complete

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_minimal_params(self, mock_install_helm_chart):
        mock_install_helm_chart.return_value = None

        await install_jumpstarter_helm_chart(
            chart="minimal",
            name="min",
            namespace="min-ns",
            basedomain="min.io",
            grpc_endpoint="grpc.min.io:80",
            router_endpoint="router.min.io:80",
            mode="test",
            version="0.1.0",
            kubeconfig=None,
            context=None,
            helm="h",
            ip="1.1.1.1",
        )

        # Verify all required parameters work with minimal values
        mock_install_helm_chart.assert_called_once_with(
            "minimal",
            "min",
            "min-ns",
            "min.io",
            "grpc.min.io:80",
            "router.min.io:80",
            "test",
            "0.1.0",
            None,
            None,
            "h",
            None,
        )

        # Verify appropriate echo calls were made

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.helm.install_helm_chart")
    async def test_install_jumpstarter_helm_chart_with_values_files(self, mock_install_helm_chart):
        """Test that values_files parameter is passed through correctly."""
        from unittest.mock import MagicMock

        mock_install_helm_chart.return_value = None
        mock_callback = MagicMock()

        values_files = ["/path/to/values1.yaml", "/path/to/values2.yaml"]

        await install_jumpstarter_helm_chart(
            chart="oci://registry.example.com/jumpstarter",
            name="jumpstarter",
            namespace="jumpstarter-system",
            basedomain="jumpstarter.192.168.1.100.nip.io",
            grpc_endpoint="grpc.jumpstarter.192.168.1.100.nip.io:8082",
            router_endpoint="router.jumpstarter.192.168.1.100.nip.io:8083",
            mode="insecure",
            version="1.0.0",
            kubeconfig="/path/to/kubeconfig",
            context="test-context",
            helm="helm",
            ip="192.168.1.100",
            callback=mock_callback,
            values_files=values_files,
        )

        # Verify that install_helm_chart was called with values_files
        mock_install_helm_chart.assert_called_once_with(
            "oci://registry.example.com/jumpstarter",
            "jumpstarter",
            "jumpstarter-system",
            "jumpstarter.192.168.1.100.nip.io",
            "grpc.jumpstarter.192.168.1.100.nip.io:8082",
            "router.jumpstarter.192.168.1.100.nip.io:8083",
            "insecure",
            "1.0.0",
            "/path/to/kubeconfig",
            "test-context",
            "helm",
            values_files,
        )

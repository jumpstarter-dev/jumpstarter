"""Tests for endpoint configuration functionality."""

from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.endpoints import configure_endpoints, get_ip_generic
from jumpstarter_kubernetes.exceptions import EndpointConfigurationError


class TestGetIpGeneric:
    """Test generic IP address retrieval."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_minikube_ip")
    async def test_get_ip_generic_minikube_success(self, mock_get_minikube_ip, mock_minikube_installed):
        mock_minikube_installed.return_value = True
        mock_get_minikube_ip.return_value = "192.168.49.2"

        result = await get_ip_generic("minikube", "minikube", "test-cluster")

        assert result == "192.168.49.2"
        mock_get_minikube_ip.assert_called_once_with("test-cluster", "minikube")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.minikube_installed")
    async def test_get_ip_generic_minikube_not_installed(self, mock_minikube_installed):
        from jumpstarter_kubernetes.exceptions import ToolNotInstalledError

        mock_minikube_installed.return_value = False

        with pytest.raises(ToolNotInstalledError, match="minikube is not installed"):
            await get_ip_generic("minikube", "minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.minikube_installed")
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_minikube_ip")
    async def test_get_ip_generic_minikube_ip_error(self, mock_get_minikube_ip, mock_minikube_installed):

        mock_minikube_installed.return_value = True
        mock_get_minikube_ip.side_effect = Exception("IP detection failed")

        with pytest.raises(EndpointConfigurationError, match="Could not determine Minikube IP address"):
            await get_ip_generic("minikube", "minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_address")
    async def test_get_ip_generic_kind_success(self, mock_get_ip_address):
        mock_get_ip_address.return_value = "10.0.0.100"

        result = await get_ip_generic("kind", "minikube", "test-cluster")

        assert result == "10.0.0.100"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_address")
    async def test_get_ip_generic_kind_zero_ip(self, mock_get_ip_address):

        mock_get_ip_address.return_value = "0.0.0.0"

        with pytest.raises(EndpointConfigurationError, match="Could not determine IP address"):
            await get_ip_generic("kind", "minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_address")
    async def test_get_ip_generic_none_cluster_type(self, mock_get_ip_address):
        mock_get_ip_address.return_value = "192.168.1.100"

        result = await get_ip_generic(None, "minikube", "test-cluster")

        assert result == "192.168.1.100"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_address")
    async def test_get_ip_generic_other_cluster_type(self, mock_get_ip_address):
        mock_get_ip_address.return_value = "172.16.0.50"

        result = await get_ip_generic("remote", "minikube", "test-cluster")

        assert result == "172.16.0.50"


class TestConfigureEndpoints:
    """Test endpoint configuration."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_all_provided(self, mock_get_ip_generic):
        # When all parameters are provided, get_ip_generic should not be called
        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip="10.0.0.100",
            basedomain="test.example.com",
            grpc_endpoint="grpc.test.example.com:9000",
            router_endpoint="router.test.example.com:9001",
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "10.0.0.100"
        assert basedomain == "test.example.com"
        assert grpc_endpoint == "grpc.test.example.com:9000"
        assert router_endpoint == "router.test.example.com:9001"
        mock_get_ip_generic.assert_not_called()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_no_ip_provided(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "192.168.49.2"

        result = await configure_endpoints(
            cluster_type="minikube",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain="test.example.com",
            grpc_endpoint="grpc.test.example.com:9000",
            router_endpoint="router.test.example.com:9001",
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "192.168.49.2"
        assert basedomain == "test.example.com"
        assert grpc_endpoint == "grpc.test.example.com:9000"
        assert router_endpoint == "router.test.example.com:9001"
        mock_get_ip_generic.assert_called_once_with("minikube", "minikube", "test-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_no_basedomain_provided(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "10.0.0.100"

        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain=None,
            grpc_endpoint="grpc.test.example.com:9000",
            router_endpoint="router.test.example.com:9001",
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "10.0.0.100"
        assert basedomain == "jumpstarter.10.0.0.100.nip.io"
        assert grpc_endpoint == "grpc.test.example.com:9000"
        assert router_endpoint == "router.test.example.com:9001"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_no_grpc_endpoint_provided(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "10.0.0.100"

        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain=None,
            grpc_endpoint=None,
            router_endpoint="router.test.example.com:9001",
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "10.0.0.100"
        assert basedomain == "jumpstarter.10.0.0.100.nip.io"
        assert grpc_endpoint == "grpc.jumpstarter.10.0.0.100.nip.io:8082"
        assert router_endpoint == "router.test.example.com:9001"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_no_router_endpoint_provided(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "10.0.0.100"

        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain=None,
            grpc_endpoint=None,
            router_endpoint=None,
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "10.0.0.100"
        assert basedomain == "jumpstarter.10.0.0.100.nip.io"
        assert grpc_endpoint == "grpc.jumpstarter.10.0.0.100.nip.io:8082"
        assert router_endpoint == "router.jumpstarter.10.0.0.100.nip.io:8083"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_all_defaults(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "192.168.1.50"

        result = await configure_endpoints(
            cluster_type="minikube",
            minikube="minikube",
            cluster_name="my-cluster",
            ip=None,
            basedomain=None,
            grpc_endpoint=None,
            router_endpoint=None,
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "192.168.1.50"
        assert basedomain == "jumpstarter.192.168.1.50.nip.io"
        assert grpc_endpoint == "grpc.jumpstarter.192.168.1.50.nip.io:8082"
        assert router_endpoint == "router.jumpstarter.192.168.1.50.nip.io:8083"
        mock_get_ip_generic.assert_called_once_with("minikube", "minikube", "my-cluster")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_custom_basedomain_with_defaults(self, mock_get_ip_generic):
        mock_get_ip_generic.return_value = "172.16.0.1"

        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip=None,
            basedomain="custom.domain.io",
            grpc_endpoint=None,
            router_endpoint=None,
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "172.16.0.1"
        assert basedomain == "custom.domain.io"
        assert grpc_endpoint == "grpc.custom.domain.io:8082"
        assert router_endpoint == "router.custom.domain.io:8083"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_ip_provided_no_auto_detection(self, mock_get_ip_generic):
        result = await configure_endpoints(
            cluster_type="kind",
            minikube="minikube",
            cluster_name="test-cluster",
            ip="192.168.100.50",
            basedomain=None,
            grpc_endpoint=None,
            router_endpoint=None,
        )

        ip, basedomain, grpc_endpoint, router_endpoint = result
        assert ip == "192.168.100.50"
        assert basedomain == "jumpstarter.192.168.100.50.nip.io"
        assert grpc_endpoint == "grpc.jumpstarter.192.168.100.50.nip.io:8082"
        assert router_endpoint == "router.jumpstarter.192.168.100.50.nip.io:8083"
        mock_get_ip_generic.assert_not_called()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.endpoints.get_ip_generic")
    async def test_configure_endpoints_ip_detection_error_propagates(self, mock_get_ip_generic):
        mock_get_ip_generic.side_effect = EndpointConfigurationError("IP detection failed")

        with pytest.raises(EndpointConfigurationError, match="IP detection failed"):
            await configure_endpoints(
                cluster_type="minikube",
                minikube="minikube",
                cluster_name="test-cluster",
                ip=None,
                basedomain=None,
                grpc_endpoint=None,
                router_endpoint=None,
            )

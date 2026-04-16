"""Tests for kubectl operations and cluster management."""

import json
from unittest.mock import patch

import pytest

from jumpstarter_kubernetes.cluster.kubectl import (
    _check_cr_instances,
    check_jumpstarter_installation,
    check_kubernetes_access,
    get_cluster_info,
    get_kubectl_contexts,
    list_clusters,
)
from jumpstarter_kubernetes.clusters import V1Alpha1ClusterInfo, V1Alpha1JumpstarterInstance
from jumpstarter_kubernetes.exceptions import JumpstarterKubernetesError


class TestCheckKubernetesAccess:
    """Test Kubernetes cluster access checking."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_kubernetes_access_success(self, mock_run_command):
        mock_run_command.return_value = (0, "cluster info", "")

        result = await check_kubernetes_access()

        assert result is True
        mock_run_command.assert_called_once_with(["kubectl", "cluster-info", "--request-timeout=5s"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_kubernetes_access_with_context(self, mock_run_command):
        mock_run_command.return_value = (0, "cluster info", "")

        result = await check_kubernetes_access(context="test-context")

        assert result is True
        mock_run_command.assert_called_once_with(
            ["kubectl", "--context", "test-context", "cluster-info", "--request-timeout=5s"]
        )

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_kubernetes_access_custom_kubectl(self, mock_run_command):
        mock_run_command.return_value = (0, "cluster info", "")

        result = await check_kubernetes_access(kubectl="custom-kubectl")

        assert result is True
        mock_run_command.assert_called_once_with(["custom-kubectl", "cluster-info", "--request-timeout=5s"])

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_kubernetes_access_failure(self, mock_run_command):
        mock_run_command.return_value = (1, "", "connection refused")

        result = await check_kubernetes_access()

        assert result is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_kubernetes_access_runtime_error(self, mock_run_command):
        mock_run_command.side_effect = RuntimeError("Command failed")

        result = await check_kubernetes_access()

        assert result is False


class TestGetKubectlContexts:
    """Test kubectl context retrieval."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_success(self, mock_run_command):
        kubectl_config = {
            "current-context": "test-context",
            "contexts": [
                {"name": "test-context", "context": {"cluster": "test-cluster", "user": "test-user"}},
                {"name": "prod-context", "context": {"cluster": "prod-cluster", "user": "prod-user"}},
            ],
            "clusters": [
                {"name": "test-cluster", "cluster": {"server": "https://test.example.com:6443"}},
                {"name": "prod-cluster", "cluster": {"server": "https://prod.example.com:6443"}},
            ],
        }
        mock_run_command.return_value = (0, json.dumps(kubectl_config), "")

        result = await get_kubectl_contexts()

        assert len(result) == 2
        assert result[0] == {
            "name": "test-context",
            "cluster": "test-cluster",
            "server": "https://test.example.com:6443",
            "user": "test-user",
            "namespace": "default",
            "current": True,
        }
        assert result[1] == {
            "name": "prod-context",
            "cluster": "prod-cluster",
            "server": "https://prod.example.com:6443",
            "user": "prod-user",
            "namespace": "default",
            "current": False,
        }

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_with_namespace(self, mock_run_command):
        kubectl_config = {
            "current-context": "test-context",
            "contexts": [
                {
                    "name": "test-context",
                    "context": {"cluster": "test-cluster", "user": "test-user", "namespace": "custom-ns"},
                }
            ],
            "clusters": [{"name": "test-cluster", "cluster": {"server": "https://test.example.com:6443"}}],
        }
        mock_run_command.return_value = (0, json.dumps(kubectl_config), "")

        result = await get_kubectl_contexts()

        assert len(result) == 1
        assert result[0]["namespace"] == "custom-ns"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_no_current_context(self, mock_run_command):
        kubectl_config = {
            "contexts": [{"name": "test-context", "context": {"cluster": "test-cluster"}}],
            "clusters": [{"name": "test-cluster", "cluster": {"server": "https://test.example.com:6443"}}],
        }
        mock_run_command.return_value = (0, json.dumps(kubectl_config), "")

        result = await get_kubectl_contexts()

        assert len(result) == 1
        assert result[0]["current"] is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_missing_cluster(self, mock_run_command):
        kubectl_config = {
            "current-context": "test-context",
            "contexts": [{"name": "test-context", "context": {"cluster": "missing-cluster"}}],
            "clusters": [],
        }
        mock_run_command.return_value = (0, json.dumps(kubectl_config), "")

        result = await get_kubectl_contexts()

        assert len(result) == 1
        assert result[0]["server"] == ""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_command_failure(self, mock_run_command):
        from jumpstarter_kubernetes.exceptions import KubeconfigError

        mock_run_command.return_value = (1, "", "permission denied")

        with pytest.raises(KubeconfigError, match="Failed to get kubectl config: permission denied"):
            await get_kubectl_contexts()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_invalid_json(self, mock_run_command):
        from jumpstarter_kubernetes.exceptions import KubeconfigError

        mock_run_command.return_value = (0, "invalid json", "")

        with pytest.raises(KubeconfigError, match="Failed to parse kubectl config"):
            await get_kubectl_contexts()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_kubectl_contexts_custom_kubectl(self, mock_run_command):
        kubectl_config = {"contexts": [], "clusters": []}
        mock_run_command.return_value = (0, json.dumps(kubectl_config), "")

        await get_kubectl_contexts(kubectl="custom-kubectl")

        mock_run_command.assert_called_once_with(["custom-kubectl", "config", "view", "-o", "json"])


class TestCheckCrInstances:
    """Test CR instance detection for Jumpstarter installation."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_found_with_namespace(self, mock_run_command):
        cr_response = {"items": [{"metadata": {"name": "jumpstarter", "namespace": "custom-ns"}}]}
        mock_run_command.return_value = (0, json.dumps(cr_response), "")

        result = await _check_cr_instances("kubectl", "test-context", "custom-ns")

        assert result == {"installed": True, "namespace": "custom-ns", "status": "installed"}

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_found_without_namespace(self, mock_run_command):
        cr_response = {"items": [{"metadata": {"name": "jumpstarter"}}]}
        mock_run_command.return_value = (0, json.dumps(cr_response), "")

        result = await _check_cr_instances("kubectl", "test-context", None)

        assert result == {"installed": True, "namespace": "unknown", "status": "installed"}

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_empty_items(self, mock_run_command):
        cr_response = {"items": []}
        mock_run_command.return_value = (0, json.dumps(cr_response), "")

        result = await _check_cr_instances("kubectl", "test-context", None)

        assert result == {}

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_nonzero_return_code(self, mock_run_command):
        mock_run_command.return_value = (1, "", "forbidden")

        result = await _check_cr_instances("kubectl", "test-context", None)

        assert result == {}

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_json_decode_error(self, mock_run_command):
        mock_run_command.return_value = (0, "not valid json", "")

        result = await _check_cr_instances("kubectl", "test-context", None)

        assert result == {}

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_cr_instances_runtime_error(self, mock_run_command):
        mock_run_command.side_effect = RuntimeError("kubectl not found")

        result = await _check_cr_instances("kubectl", "test-context", None)

        assert result == {}


class TestCheckJumpstarterInstallation:
    """Test Jumpstarter installation checking via CRD detection."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_crds_only(self, mock_run_command):
        crds_response = {"items": [{"metadata": {"name": "exporters.jumpstarter.dev"}}]}

        mock_run_command.return_value = (0, json.dumps(crds_response), "")

        result = await check_jumpstarter_installation("test-context")

        assert result.has_crds is True
        assert result.installed is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_with_cr_instances(self, mock_run_command):
        crds_response = {"items": [
            {"metadata": {"name": "exporters.jumpstarter.dev"}},
            {"metadata": {"name": "jumpstarters.operator.jumpstarter.dev"}},
        ]}
        cr_response = {"items": [{"metadata": {"name": "jumpstarter", "namespace": "jumpstarter"}}]}

        mock_run_command.side_effect = [
            (0, json.dumps(crds_response), ""),
            (0, json.dumps(cr_response), ""),
        ]

        result = await check_jumpstarter_installation("test-context")

        assert result.has_crds is True
        assert result.installed is True
        assert result.status == "installed"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_no_crds(self, mock_run_command):
        mock_run_command.return_value = (0, '{"items": []}', "")

        result = await check_jumpstarter_installation("test-context")

        assert result.installed is False
        assert result.has_crds is False

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_command_failure(self, mock_run_command):
        mock_run_command.side_effect = RuntimeError("kubectl not found")

        result = await check_jumpstarter_installation("test-context")

        assert result.installed is False
        assert result.has_crds is False
        assert result.error is not None
        assert "kubectl not found" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_nonzero_exit(self, mock_run_command):
        mock_run_command.return_value = (1, "", "forbidden")

        result = await check_jumpstarter_installation("test-context")

        assert result.installed is False
        assert result.has_crds is False
        assert result.error is not None
        assert "forbidden" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_custom_namespace(self, mock_run_command):
        crds_response = {"items": [
            {"metadata": {"name": "exporters.jumpstarter.dev"}},
            {"metadata": {"name": "jumpstarters.operator.jumpstarter.dev"}},
        ]}
        cr_response = {"items": [{"metadata": {"name": "jumpstarter", "namespace": "custom-ns"}}]}

        mock_run_command.side_effect = [
            (0, json.dumps(crds_response), ""),
            (0, json.dumps(cr_response), ""),
        ]

        result = await check_jumpstarter_installation("test-context", namespace="custom-ns")

        assert result.installed is True
        assert result.namespace == "custom-ns"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_json_decode_error(self, mock_run_command):
        mock_run_command.return_value = (0, "not valid json at all", "")

        result = await check_jumpstarter_installation("test-context")

        assert result.installed is False
        assert result.error is not None
        assert "Failed to parse output" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_stdout_without_json_prefix(self, mock_run_command):
        crds_json = json.dumps({"items": [{"metadata": {"name": "exporters.jumpstarter.dev"}}]})
        mock_run_command.return_value = (0, crds_json, "")

        result = await check_jumpstarter_installation("test-context")

        assert result.has_crds is True

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_stdout_with_warning_prefix(self, mock_run_command):
        crds_json = json.dumps({"items": [{"metadata": {"name": "exporters.jumpstarter.dev"}}]})
        stdout_with_warning = f"Warning: some kubectl warning\n{crds_json}"
        mock_run_command.return_value = (0, stdout_with_warning, "")

        result = await check_jumpstarter_installation("test-context")

        assert result.has_crds is True

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_check_jumpstarter_installation_cr_check_empty_items(self, mock_run_command):
        crds_response = {"items": [
            {"metadata": {"name": "jumpstarters.operator.jumpstarter.dev"}},
        ]}
        cr_response = {"items": []}

        mock_run_command.side_effect = [
            (0, json.dumps(crds_response), ""),
            (0, json.dumps(cr_response), ""),
        ]

        result = await check_jumpstarter_installation("test-context")

        assert result.has_crds is True
        assert result.installed is False


class TestGetClusterInfo:
    """Test cluster info retrieval."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    @patch("jumpstarter_kubernetes.cluster.kubectl.check_jumpstarter_installation")
    async def test_get_cluster_info_success(self, mock_check_jumpstarter, mock_run_command, mock_get_contexts):
        # Mock the context lookup
        mock_get_contexts.return_value = [
            {
                "name": "test-context",
                "cluster": "test-cluster",
                "server": "https://test.example.com",
                "user": "test-user",
                "namespace": "default",
                "current": False,
            }
        ]

        version_output = {"serverVersion": {"gitVersion": "v1.28.0"}}
        mock_run_command.return_value = (0, json.dumps(version_output), "")
        mock_check_jumpstarter.return_value = V1Alpha1JumpstarterInstance(installed=True, version="1.0.0")

        result = await get_cluster_info("test-context")

        assert isinstance(result, V1Alpha1ClusterInfo)
        assert result.name == "test-context"
        assert result.cluster == "test-cluster"
        assert result.server == "https://test.example.com"
        assert result.accessible is True
        assert result.version == "v1.28.0"
        assert result.jumpstarter.installed is True

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_get_cluster_info_inaccessible(self, mock_get_contexts):
        # Mock get_kubectl_contexts to fail
        mock_get_contexts.side_effect = JumpstarterKubernetesError("Failed to get kubectl config: connection refused")

        result = await get_cluster_info("test-context")

        assert result.accessible is False
        assert "Failed to get cluster info:" in result.error
        assert "Failed to get kubectl config: connection refused" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_get_cluster_info_invalid_json(self, mock_get_contexts):
        error_msg = "Failed to parse kubectl config: Expecting value: line 1 column 1 (char 0)"
        mock_get_contexts.side_effect = JumpstarterKubernetesError(error_msg)

        result = await get_cluster_info("test-context")

        assert result.accessible is False
        assert "Failed to get cluster info" in result.error
        assert "Failed to parse kubectl config" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_get_cluster_info_context_not_found(self, mock_get_contexts):
        mock_get_contexts.return_value = [
            {"name": "other-context", "cluster": "other", "server": "https://other", "user": "u", "namespace": "default", "current": False}
        ]

        result = await get_cluster_info("missing-context")

        assert result.name == "missing-context"
        assert result.accessible is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    @patch("jumpstarter_kubernetes.cluster.kubectl.check_jumpstarter_installation")
    async def test_get_cluster_info_inaccessible_cluster(self, mock_check_jumpstarter, mock_run_command, mock_get_contexts):
        mock_get_contexts.return_value = [
            {"name": "test-context", "cluster": "test-cluster", "server": "https://test.example.com", "user": "test-user", "namespace": "default", "current": False}
        ]
        mock_run_command.return_value = (1, "", "connection refused")

        result = await get_cluster_info("test-context")

        assert result.accessible is False
        assert result.jumpstarter.installed is False
        assert result.jumpstarter.error == "Cluster not accessible"
        mock_check_jumpstarter.assert_not_called()

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    @patch("jumpstarter_kubernetes.cluster.kubectl.check_jumpstarter_installation")
    async def test_get_cluster_info_version_parse_failure(self, mock_check_jumpstarter, mock_run_command, mock_get_contexts):
        mock_get_contexts.return_value = [
            {"name": "test-context", "cluster": "test-cluster", "server": "https://test.example.com", "user": "test-user", "namespace": "default", "current": False}
        ]
        mock_run_command.return_value = (0, "not json", "")
        mock_check_jumpstarter.return_value = V1Alpha1JumpstarterInstance(installed=False)

        result = await get_cluster_info("test-context")

        assert result.accessible is True
        assert result.version == "unknown"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.run_command")
    async def test_get_cluster_info_version_command_runtime_error(self, mock_run_command, mock_get_contexts):
        mock_get_contexts.return_value = [
            {"name": "test-context", "cluster": "test-cluster", "server": "https://test.example.com", "user": "test-user", "namespace": "default", "current": False}
        ]
        mock_run_command.side_effect = RuntimeError("command failed")

        result = await get_cluster_info("test-context")

        assert result.accessible is False
        assert result.version is None


class TestListClusters:
    """Test cluster listing functionality."""

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_cluster_info")
    async def test_list_clusters_success(self, mock_get_cluster_info, mock_get_contexts):
        contexts = [
            {
                "name": "test-context",
                "cluster": "test-cluster",
                "server": "https://test.example.com",
                "user": "test-user",
                "current": True,
            }
        ]
        mock_get_contexts.return_value = contexts

        cluster_info = V1Alpha1ClusterInfo(
            name="test-context",
            cluster="test-cluster",
            server="https://test.example.com",
            user="test-user",
            namespace="default",
            is_current=True,
            type="kind",
            accessible=True,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
        )
        mock_get_cluster_info.return_value = cluster_info

        result = await list_clusters()

        assert len(result.items) == 1
        assert result.items[0].name == "test-context"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_list_clusters_no_contexts(self, mock_get_contexts):
        mock_get_contexts.return_value = []

        result = await list_clusters()

        assert len(result.items) == 0

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_list_clusters_context_error(self, mock_get_contexts):
        mock_get_contexts.side_effect = JumpstarterKubernetesError("No kubeconfig found")

        result = await list_clusters()

        assert len(result.items) == 1
        assert result.items[0].error == "Failed to list clusters: No kubeconfig found"

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    async def test_list_clusters_custom_parameters(self, mock_get_contexts):
        mock_get_contexts.return_value = []

        await list_clusters(kubectl="custom-kubectl", minikube="custom-minikube")

        mock_get_contexts.assert_called_once_with("custom-kubectl")

    @pytest.mark.asyncio
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_kubectl_contexts")
    @patch("jumpstarter_kubernetes.cluster.kubectl.get_cluster_info")
    async def test_list_clusters_with_type_filter(self, mock_get_cluster_info, mock_get_contexts):
        mock_get_contexts.return_value = [
            {"name": "kind-ctx", "cluster": "kind-cluster", "server": "https://kind", "user": "u", "current": True},
            {"name": "remote-ctx", "cluster": "remote-cluster", "server": "https://remote", "user": "u", "current": False},
        ]

        kind_info = V1Alpha1ClusterInfo(
            name="kind-ctx", cluster="kind-cluster", server="https://kind", user="u",
            namespace="default", is_current=True, type="kind", accessible=True,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
        )
        remote_info = V1Alpha1ClusterInfo(
            name="remote-ctx", cluster="remote-cluster", server="https://remote", user="u",
            namespace="default", is_current=False, type="remote", accessible=False,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
        )
        mock_get_cluster_info.side_effect = [kind_info, remote_info]

        result = await list_clusters(cluster_type_filter="kind")

        assert len(result.items) == 1
        assert result.items[0].name == "kind-ctx"

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from .get import get

# `get client` / `get exporter` / `get lease` run on the Rust core (forwarded via FFI) and are
# covered by the Rust admin tests (output rows + `--devices`) + the e2e suite. Only the native
# `cluster` / `clusters` subcommands are tested here.


@patch("jumpstarter_cli_admin.get.get_cluster_info")
def test_get_cluster_by_name(get_cluster_info_mock: AsyncMock):
    from jumpstarter_kubernetes import V1Alpha1ClusterInfo, V1Alpha1JumpstarterInstance

    runner = CliRunner()
    cluster_info = V1Alpha1ClusterInfo(
        name="kind-test",
        cluster="kind-kind-test",
        server="https://127.0.0.1:6443",
        user="kind-kind-test",
        namespace="default",
        is_current=True,
        type="kind",
        accessible=True,
        version="1.28.0",
        jumpstarter=V1Alpha1JumpstarterInstance(installed=True, version="0.1.0", namespace="jumpstarter"),
    )
    get_cluster_info_mock.return_value = cluster_info
    result = runner.invoke(get, ["cluster", "kind-test"])
    assert result.exit_code == 0
    assert "kind-test" in result.output


@patch("jumpstarter_cli_admin.get.get_cluster_info")
def test_get_cluster_not_found(get_cluster_info_mock: AsyncMock):
    from jumpstarter_kubernetes import V1Alpha1ClusterInfo, V1Alpha1JumpstarterInstance

    runner = CliRunner()
    cluster_info = V1Alpha1ClusterInfo(
        name="nonexistent",
        cluster="nonexistent",
        server="",
        user="",
        namespace="default",
        is_current=False,
        type="remote",
        accessible=False,
        jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
        error="context not found",
    )
    get_cluster_info_mock.return_value = cluster_info
    result = runner.invoke(get, ["cluster", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


@patch("jumpstarter_cli_admin.get.get_cluster_info")
def test_get_cluster_error(get_cluster_info_mock: AsyncMock):
    runner = CliRunner()
    get_cluster_info_mock.side_effect = Exception("Unexpected error")
    result = runner.invoke(get, ["cluster", "test"])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


@patch("jumpstarter_cli_admin.get.list_clusters")
def test_get_clusters_list(list_clusters_mock: AsyncMock):
    from jumpstarter_kubernetes import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance

    runner = CliRunner()
    cluster_list = V1Alpha1ClusterList(
        items=[
            V1Alpha1ClusterInfo(
                name="kind-test",
                cluster="kind-kind-test",
                server="https://127.0.0.1:6443",
                user="kind-kind-test",
                namespace="default",
                is_current=True,
                type="kind",
                accessible=True,
                version="1.28.0",
                jumpstarter=V1Alpha1JumpstarterInstance(installed=True, version="0.1.0", namespace="jumpstarter"),
            ),
            V1Alpha1ClusterInfo(
                name="minikube",
                cluster="minikube",
                server="https://192.168.49.2:8443",
                user="minikube",
                namespace="default",
                is_current=False,
                type="minikube",
                accessible=True,
                version="1.28.0",
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            ),
        ]
    )
    list_clusters_mock.return_value = cluster_list
    result = runner.invoke(get, ["clusters"])
    assert result.exit_code == 0
    assert "kind-test" in result.output
    assert "minikube" in result.output


@patch("jumpstarter_cli_admin.get.list_clusters")
def test_get_clusters_error(list_clusters_mock: AsyncMock):
    runner = CliRunner()
    list_clusters_mock.side_effect = Exception("Unexpected error")
    result = runner.invoke(get, ["clusters"])
    assert result.exit_code == 1
    assert "error" in result.output.lower()


@patch("jumpstarter_cli_admin.get.list_clusters")
def test_get_cluster_without_name_lists_all(list_clusters_mock: AsyncMock):
    from jumpstarter_kubernetes import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance

    runner = CliRunner()
    cluster_list = V1Alpha1ClusterList(
        items=[
            V1Alpha1ClusterInfo(
                name="kind-test",
                cluster="kind-kind-test",
                server="https://127.0.0.1:6443",
                user="kind-kind-test",
                namespace="default",
                is_current=True,
                type="kind",
                accessible=True,
                version="1.28.0",
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            ),
        ]
    )
    list_clusters_mock.return_value = cluster_list
    result = runner.invoke(get, ["cluster"])
    assert result.exit_code == 0
    assert "kind-test" in result.output


@patch("jumpstarter_cli_admin.get.list_clusters")
def test_get_clusters_command_calls_list_clusters(list_clusters_mock: AsyncMock):
    from jumpstarter_kubernetes import V1Alpha1ClusterList

    runner = CliRunner()
    list_clusters_mock.return_value = V1Alpha1ClusterList(items=[])
    result = runner.invoke(get, ["clusters"])
    assert result.exit_code == 0
    list_clusters_mock.assert_called_once()

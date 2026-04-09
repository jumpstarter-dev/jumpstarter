from unittest.mock import MagicMock

from .clusters import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance


def test_cluster_info_rich_add_columns():
    """Test that ClusterInfo can add columns to a rich table"""
    mock_table = MagicMock()
    V1Alpha1ClusterInfo.rich_add_columns(mock_table)
    assert mock_table.add_column.call_count == 7
    mock_table.add_column.assert_any_call("CURRENT")
    mock_table.add_column.assert_any_call("NAME")
    mock_table.add_column.assert_any_call("TYPE")
    mock_table.add_column.assert_any_call("STATUS")
    mock_table.add_column.assert_any_call("JUMPSTARTER")
    mock_table.add_column.assert_any_call("VERSION")
    mock_table.add_column.assert_any_call("NAMESPACE")


def test_cluster_info_rich_add_rows_current_running_installed():
    """Test rich table row for current, running cluster with Jumpstarter installed"""
    mock_table = MagicMock()
    cluster_info = V1Alpha1ClusterInfo(
        name="test-cluster",
        cluster="test-cluster",
        server="https://127.0.0.1:6443",
        user="test-user",
        namespace="default",
        is_current=True,
        type="kind",
        accessible=True,
        version="1.28.0",
        jumpstarter=V1Alpha1JumpstarterInstance(
            installed=True,
            version="0.1.0",
            namespace="jumpstarter",
        ),
    )
    cluster_info.rich_add_rows(mock_table)
    mock_table.add_row.assert_called_once()
    args = mock_table.add_row.call_args[0]
    assert args[0] == "*"  # current
    assert args[1] == "test-cluster"  # name
    assert args[2] == "kind"  # type
    assert args[3] == "Running"  # status
    assert args[4] == "Yes"  # jumpstarter
    assert args[5] == "0.1.0"  # version
    assert args[6] == "jumpstarter"  # namespace


def test_cluster_info_rich_add_rows_not_current_stopped_not_installed():
    """Test rich table row for non-current, stopped cluster without Jumpstarter"""
    mock_table = MagicMock()
    cluster_info = V1Alpha1ClusterInfo(
        name="test-cluster",
        cluster="test-cluster",
        server="https://127.0.0.1:6443",
        user="test-user",
        namespace="default",
        is_current=False,
        type="minikube",
        accessible=False,
        jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
    )
    cluster_info.rich_add_rows(mock_table)
    mock_table.add_row.assert_called_once()
    args = mock_table.add_row.call_args[0]
    assert args[0] == ""  # not current
    assert args[1] == "test-cluster"  # name
    assert args[2] == "minikube"  # type
    assert args[3] == "Stopped"  # status
    assert args[4] == "No"  # jumpstarter
    assert args[5] == "-"  # no version
    assert args[6] == "-"  # no namespace


def test_cluster_info_rich_add_rows_with_jumpstarter_error():
    """Test rich table row for cluster with Jumpstarter installation error"""
    mock_table = MagicMock()
    cluster_info = V1Alpha1ClusterInfo(
        name="test-cluster",
        cluster="test-cluster",
        server="https://127.0.0.1:6443",
        user="test-user",
        namespace="default",
        is_current=True,
        type="kind",
        accessible=True,
        jumpstarter=V1Alpha1JumpstarterInstance(
            installed=False,
            error="Failed to connect",
        ),
    )
    cluster_info.rich_add_rows(mock_table)
    mock_table.add_row.assert_called_once()
    args = mock_table.add_row.call_args[0]
    assert args[4] == "Error"  # jumpstarter status shows error


def test_cluster_info_rich_add_names():
    """Test that ClusterInfo can add names for name output"""
    cluster_info = V1Alpha1ClusterInfo(
        name="test-cluster",
        cluster="test-cluster",
        server="https://127.0.0.1:6443",
        user="test-user",
        namespace="default",
        is_current=True,
        type="kind",
        accessible=True,
        jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
    )
    names = []
    cluster_info.rich_add_names(names)
    assert names == ["cluster/test-cluster"]


def test_cluster_list_rich_add_columns():
    """Test that ClusterList can add columns to a rich table"""
    mock_table = MagicMock()
    V1Alpha1ClusterList.rich_add_columns(mock_table)
    assert mock_table.add_column.call_count == 7


def test_cluster_list_rich_add_rows():
    """Test that ClusterList can add rows for multiple clusters"""
    mock_table = MagicMock()
    cluster_list = V1Alpha1ClusterList(
        items=[
            V1Alpha1ClusterInfo(
                name="cluster1",
                cluster="cluster1",
                server="https://127.0.0.1:6443",
                user="user1",
                namespace="default",
                is_current=True,
                type="kind",
                accessible=True,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=True),
            ),
            V1Alpha1ClusterInfo(
                name="cluster2",
                cluster="cluster2",
                server="https://192.168.49.2:8443",
                user="user2",
                namespace="default",
                is_current=False,
                type="minikube",
                accessible=False,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            ),
        ]
    )
    cluster_list.rich_add_rows(mock_table)
    assert mock_table.add_row.call_count == 2


def test_cluster_list_rich_add_names():
    """Test that ClusterList can add names for all clusters"""
    cluster_list = V1Alpha1ClusterList(
        items=[
            V1Alpha1ClusterInfo(
                name="cluster1",
                cluster="cluster1",
                server="https://127.0.0.1:6443",
                user="user1",
                namespace="default",
                is_current=True,
                type="kind",
                accessible=True,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            ),
            V1Alpha1ClusterInfo(
                name="cluster2",
                cluster="cluster2",
                server="https://192.168.49.2:8443",
                user="user2",
                namespace="default",
                is_current=False,
                type="minikube",
                accessible=False,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            ),
        ]
    )
    names = []
    cluster_list.rich_add_names(names)
    assert names == ["cluster/cluster1", "cluster/cluster2"]

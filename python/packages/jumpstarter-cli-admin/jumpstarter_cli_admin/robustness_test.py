import click
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

ALLOWED_CLI_EXCEPTIONS = (
    SystemExit,
    click.exceptions.BadParameter,
    click.exceptions.UsageError,
    click.exceptions.MissingParameter,
    click.ClickException,
    click.Abort,
    OSError,
    ConnectionError,
    TimeoutError,
)


def _is_network_or_k8s_error(exc: Exception) -> bool:
    module = type(exc).__module__ or ""
    return any(mod in module for mod in ("aiohttp", "kubernetes", "urllib3", "socket", "ssl"))


def _invoke_admin(subcommand_args: list[str]) -> None:
    from . import admin

    runner = CliRunner()
    try:
        runner.invoke(admin, subcommand_args, catch_exceptions=False)
    except ALLOWED_CLI_EXCEPTIONS:
        pass
    except Exception as exc:
        if _is_network_or_k8s_error(exc):
            pass
        else:
            raise AssertionError(f"admin {' '.join(subcommand_args[:3])} crashed: {type(exc).__name__}: {exc}") from exc


class TestAdminCreateClientRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_create_client_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["create", "client", *args])


class TestAdminCreateExporterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_create_exporter_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["create", "exporter", *args])


class TestAdminCreateClusterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_create_cluster_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["create", "cluster", *args])


class TestAdminDeleteClientRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_delete_client_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["delete", "client", *args])


class TestAdminDeleteExporterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_delete_exporter_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["delete", "exporter", *args])


class TestAdminDeleteClusterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_delete_cluster_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["delete", "cluster", *args])


class TestAdminGetClientRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_client_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["get", "client", *args])


class TestAdminGetExporterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_exporter_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["get", "exporter", *args])


class TestAdminGetLeaseRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_lease_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["get", "lease", *args])


class TestAdminGetClusterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_cluster_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["get", "cluster", *args])


class TestAdminGetClustersRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_get_clusters_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["get", "clusters", *args])


class TestAdminImportClientRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_import_client_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["import", "client", *args])


class TestAdminImportExporterRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_import_exporter_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["import", "exporter", *args])


class TestAdminRotateClientRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_rotate_client_never_crashes(self, args: list[str]) -> None:
        _invoke_admin(["rotate", "client", *args])


class TestAdminRobustnessTopLevel:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_admin_never_crashes_on_garbage(self, args: list[str]) -> None:
        _invoke_admin(args)

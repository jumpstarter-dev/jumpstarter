import inspect
from datetime import timedelta
from unittest.mock import Mock, patch

import click
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel


class FakeLease(BaseModel):
    name: str = "test-lease"
    status: str = "active"


class FakeLeaseList(BaseModel):
    leases: list = []

    def filter_by_selector(self, selector):
        return self

    def filter_by_client(self, name):
        return self


class FakeExporterList(BaseModel):
    exporters: list = []


ALLOWED_EXCEPTIONS = (
    SystemExit,
    click.BadParameter,
    click.UsageError,
    click.MissingParameter,
    click.ClickException,
    click.Abort,
)


class TestCreateLeaseDeepExecution:
    def test_valid_args_with_mocked_backend(self) -> None:
        from jumpstarter_cli.create import create_lease

        config = Mock()
        config.create_lease.return_value = FakeLease()

        with patch("jumpstarter_cli.create.model_print"):
            inspect.unwrap(create_lease.callback)(
                config=config,
                selector="board=rpi4",
                exporter_name=None,
                duration=timedelta(hours=1),
                begin_time=None,
                lease_id=None,
                tags=(),
                output="yaml",
            )
        config.create_lease.assert_called_once()

    def test_extremely_long_duration(self) -> None:
        from jumpstarter_cli.create import create_lease

        config = Mock()
        config.create_lease.return_value = FakeLease()

        with patch("jumpstarter_cli.create.model_print"):
            inspect.unwrap(create_lease.callback)(
                config=config,
                selector="board=rpi4",
                exporter_name=None,
                duration=timedelta(hours=99999999),
                begin_time=None,
                lease_id=None,
                tags=(),
                output="yaml",
            )
        config.create_lease.assert_called_once()

    @given(selector=st.text(min_size=1, max_size=100))
    def test_arbitrary_selector_with_mocked_backend(self, selector: str) -> None:
        from jumpstarter_cli.create import create_lease

        config = Mock()
        config.create_lease.return_value = FakeLease()

        try:
            with patch("jumpstarter_cli.create.model_print"):
                inspect.unwrap(create_lease.callback)(
                    config=config,
                    selector=selector,
                    exporter_name=None,
                    duration=timedelta(minutes=5),
                    begin_time=None,
                    lease_id=None,
                    tags=(),
                    output="yaml",
                )
        except ALLOWED_EXCEPTIONS:
            pass

    @given(name=st.text(min_size=1, max_size=100))
    def test_arbitrary_exporter_name_with_mocked_backend(self, name: str) -> None:
        from jumpstarter_cli.create import create_lease

        config = Mock()
        config.create_lease.return_value = FakeLease()

        try:
            with patch("jumpstarter_cli.create.model_print"):
                inspect.unwrap(create_lease.callback)(
                    config=config,
                    selector=None,
                    exporter_name=name,
                    duration=timedelta(minutes=5),
                    begin_time=None,
                    lease_id=None,
                    tags=(),
                    output="yaml",
                )
        except ALLOWED_EXCEPTIONS:
            pass

    @given(tags=st.lists(st.text(max_size=50), max_size=5))
    def test_arbitrary_tags_with_mocked_backend(self, tags: list[str]) -> None:
        from jumpstarter_cli.create import create_lease

        config = Mock()
        config.create_lease.return_value = FakeLease()

        try:
            with patch("jumpstarter_cli.create.model_print"):
                inspect.unwrap(create_lease.callback)(
                    config=config,
                    selector="board=test",
                    exporter_name=None,
                    duration=timedelta(minutes=5),
                    begin_time=None,
                    lease_id=None,
                    tags=tuple(tags),
                    output="yaml",
                )
        except ALLOWED_EXCEPTIONS:
            pass


class TestDeleteLeasesDeepExecution:
    def test_delete_by_names_with_mocked_backend(self) -> None:
        from jumpstarter_cli.delete import delete_leases

        config = Mock()
        config.delete_lease.return_value = None

        inspect.unwrap(delete_leases.callback)(
            config=config,
            names=("lease-1", "lease-2"),
            selector=None,
            delete_all=False,
            all_clients=False,
            output="default",
        )
        assert config.delete_lease.call_count == 2

    def test_delete_no_criteria_raises(self) -> None:
        from jumpstarter_cli.delete import delete_leases

        config = Mock()

        with pytest.raises(click.ClickException, match="One of NAMES"):
            inspect.unwrap(delete_leases.callback)(
                config=config,
                names=(),
                selector=None,
                delete_all=False,
                all_clients=False,
                output="default",
            )

    @given(names=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5))
    def test_arbitrary_names_with_mocked_backend(self, names: list[str]) -> None:
        from jumpstarter_cli.delete import delete_leases

        config = Mock()
        config.delete_lease.return_value = None

        try:
            inspect.unwrap(delete_leases.callback)(
                config=config,
                names=tuple(names),
                selector=None,
                delete_all=False,
                all_clients=False,
                output="default",
            )
        except ALLOWED_EXCEPTIONS:
            pass

    def test_delete_all_with_empty_result(self) -> None:
        from jumpstarter_cli.delete import delete_leases

        config = Mock()
        config.list_leases.return_value = FakeLeaseList()
        config.metadata = Mock()
        config.metadata.name = "test-client"

        with pytest.raises(click.ClickException, match="no leases found"):
            inspect.unwrap(delete_leases.callback)(
                config=config,
                names=(),
                selector=None,
                delete_all=True,
                all_clients=False,
                output="default",
            )


class TestGetExportersDeepExecution:
    def test_get_exporters_with_mocked_backend(self) -> None:
        from jumpstarter_cli.get import get_exporters

        config = Mock()
        config.list_exporters.return_value = FakeExporterList()

        with patch("jumpstarter_cli.get.model_print"):
            inspect.unwrap(get_exporters.callback)(
                config=config,
                selector=None,
                output="yaml",
                with_options=[],
            )
        config.list_exporters.assert_called_once()

    @given(selector=st.text(max_size=100))
    def test_arbitrary_selector(self, selector: str) -> None:
        from jumpstarter_cli.get import get_exporters

        config = Mock()
        config.list_exporters.return_value = FakeExporterList()

        try:
            with patch("jumpstarter_cli.get.model_print"):
                inspect.unwrap(get_exporters.callback)(
                    config=config,
                    selector=selector or None,
                    output="yaml",
                    with_options=[],
                )
        except ALLOWED_EXCEPTIONS:
            pass

    def test_get_exporters_with_all_options(self) -> None:
        from jumpstarter_cli.get import get_exporters

        config = Mock()
        config.list_exporters.return_value = FakeExporterList()

        with patch("jumpstarter_cli.get.model_print"):
            inspect.unwrap(get_exporters.callback)(
                config=config,
                selector=None,
                output="yaml",
                with_options=["leases", "online", "status"],
            )

        config.list_exporters.assert_called_once_with(
            filter=None,
            include_leases=True,
            include_online=True,
            include_status=True,
        )


class TestGetLeasesDeepExecution:
    def test_get_leases_with_mocked_backend(self) -> None:
        from jumpstarter_cli.get import get_leases

        config = Mock()
        config.list_leases.return_value = FakeLeaseList()
        config.metadata = Mock()
        config.metadata.name = "test-client"

        with patch("jumpstarter_cli.get.model_print"):
            inspect.unwrap(get_leases.callback)(
                config=config,
                selector=None,
                output="yaml",
                show_all=False,
                all_clients=False,
                tag_filter=None,
            )

    @given(selector=st.text(max_size=100))
    def test_arbitrary_selector_with_mocked_backend(self, selector: str) -> None:
        from jumpstarter_cli.get import get_leases

        config = Mock()
        config.list_leases.return_value = FakeLeaseList()
        config.metadata = Mock()
        config.metadata.name = "test-client"

        try:
            with patch("jumpstarter_cli.get.model_print"):
                inspect.unwrap(get_leases.callback)(
                    config=config,
                    selector=selector or None,
                    output="yaml",
                    show_all=False,
                    all_clients=False,
                    tag_filter=None,
                )
        except ALLOWED_EXCEPTIONS:
            pass

    @given(tag_filter=st.text(max_size=100))
    def test_arbitrary_tag_filter(self, tag_filter: str) -> None:
        from jumpstarter_cli.get import get_leases

        config = Mock()
        config.list_leases.return_value = FakeLeaseList()
        config.metadata = Mock()
        config.metadata.name = "test-client"

        try:
            with patch("jumpstarter_cli.get.model_print"):
                inspect.unwrap(get_leases.callback)(
                    config=config,
                    selector=None,
                    output="yaml",
                    show_all=False,
                    all_clients=False,
                    tag_filter=tag_filter or None,
                )
        except ALLOWED_EXCEPTIONS:
            pass

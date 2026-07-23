import inspect
from datetime import timedelta
from unittest.mock import Mock, patch

import click
import pytest

from jumpstarter_cli.create import create_lease


def test_create_lease_passes_exporter_name_to_config():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print") as model_print:
        # Skip Click config loading wrapper and call the command body directly.
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector=None,
            exporter_name="laptop-test-exporter",
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector=None,
        exporter_name="laptop-test-exporter",
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
        allow_disabled=False,
        context=None,
    )
    model_print.assert_called_once_with(lease, "yaml")


def test_create_lease_requires_selector_or_name():
    with pytest.raises(click.UsageError, match="one of --selector/-l or --name/-n is required"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector=None,
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )


def test_create_lease_passes_tags_to_config():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=("team=devops", "ci-job=12345"),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags={"team": "devops", "ci-job": "12345"},
        allow_disabled=False,
        context=None,
    )


def test_create_lease_empty_tags_passes_none():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
        allow_disabled=False,
        context=None,
    )


def test_create_lease_invalid_tag_format():
    with pytest.raises(click.UsageError, match="Invalid tag format"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=("invalid-no-equals",),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )


def test_create_lease_passes_context_to_config():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=("build_id=nightly-42", "image_digest=sha256:abc"),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
        allow_disabled=False,
        context={"build_id": "nightly-42", "image_digest": "sha256:abc"},
    )


def test_create_lease_empty_context_passes_none():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"):
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    config.create_lease.assert_called_once_with(
        selector="board=rpi4",
        exporter_name=None,
        duration=timedelta(minutes=5),
        begin_time=None,
        lease_id=None,
        tags=None,
        allow_disabled=False,
        context=None,
    )


def test_create_lease_invalid_context_format():
    with pytest.raises(click.UsageError, match="Invalid context format"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=("no-equals-sign",),
            output="yaml",
        )


def test_create_lease_context_key_too_long():
    long_key = "k" * 33
    with pytest.raises(click.UsageError, match="Context key too long"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(f"{long_key}=val",),
            output="yaml",
        )


def test_create_lease_context_value_too_long():
    long_val = "v" * 65
    with pytest.raises(click.UsageError, match="Context value too long"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(f"key={long_val}",),
            output="yaml",
        )


def test_create_lease_too_many_context_entries():
    entries = tuple(f"key{i}=val{i}" for i in range(9))
    with pytest.raises(click.UsageError, match="Too many context entries"):
        inspect.unwrap(create_lease.callback)(
            config=Mock(),
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=entries,
            output="yaml",
        )


def test_create_lease_emits_deprecated_label_warnings():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {"legacy-board": "Use board instead"}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"), patch("jumpstarter_cli.create.click") as mock_click:
        mock_click.style.side_effect = lambda text, **kwargs: text
        mock_click.UsageError = click.UsageError
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="legacy-board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    mock_click.style.assert_called_once_with("Warning: ", fg="yellow")
    mock_click.echo.assert_called_once()
    assert mock_click.echo.call_args[1]["err"] is True
    warning_msg = mock_click.echo.call_args[0][0]
    assert "legacy-board" in warning_msg
    assert "deprecated" in warning_msg
    assert "Use board instead" in warning_msg


def test_create_lease_emits_deprecated_label_warning_without_message():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {"old-key": ""}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"), patch("jumpstarter_cli.create.click") as mock_click:
        mock_click.style.side_effect = lambda text, **kwargs: text
        mock_click.UsageError = click.UsageError
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="old-key=val",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    mock_click.echo.assert_called_once()
    warning_msg = mock_click.echo.call_args[0][0]
    assert "old-key" in warning_msg
    assert "deprecated" in warning_msg
    assert ":" not in warning_msg.split("deprecated")[1]


def test_create_lease_no_warnings_when_no_deprecated_labels():
    config = Mock()
    lease = Mock()
    lease.deprecated_labels = {}
    config.create_lease.return_value = lease

    with patch("jumpstarter_cli.create.model_print"), patch("jumpstarter_cli.create.click") as mock_click:
        mock_click.UsageError = click.UsageError
        inspect.unwrap(create_lease.callback)(
            config=config,
            selector="board=rpi4",
            exporter_name=None,
            duration=timedelta(minutes=5),
            begin_time=None,
            lease_id=None,
            tags=(),
            allow_disabled=False,
            context_entries=(),
            output="yaml",
        )

    mock_click.echo.assert_not_called()

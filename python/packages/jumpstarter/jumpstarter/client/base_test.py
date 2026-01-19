"""Tests for StubDriverClient."""

import logging
from contextlib import ExitStack
from unittest.mock import MagicMock, create_autospec
from uuid import uuid4

import pytest
from anyio.from_thread import BlockingPortal

from .base import StubDriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


class MissingClientDriver(Driver):
    """Test driver that returns a non-existent client class path."""

    @classmethod
    def client(cls) -> str:
        return "nonexistent_driver_package.client.NonExistentClient"


def create_stub_client(class_path: str) -> StubDriverClient:
    """Create a StubDriverClient with minimal mocking for testing."""
    return StubDriverClient(
        uuid=uuid4(),
        labels={"jumpstarter.dev/client": class_path},
        stub=MagicMock(),
        portal=create_autospec(BlockingPortal, instance=True),
        stack=ExitStack(),
    )


def test_missing_driver_logs_warning_and_creates_stub(caplog):
    """Test that a missing driver logs a warning and creates a StubDriverClient."""
    expected_class_path = "nonexistent_driver_package.client.NonExistentClient"
    with caplog.at_level(logging.WARNING):
        with serve(MissingClientDriver()) as client:
            # Should have logged a warning with the exact class path from MissingDriverError
            assert f"Driver client '{expected_class_path}' is not available." in caplog.text

            # Should have created a StubDriverClient
            assert isinstance(client, StubDriverClient)

            # Using the stub should raise an error
            with pytest.raises(ImportError):
                client.call("some_method")


def test_stub_driver_client_streamingcall_raises():
    """Test that streamingcall() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info:
        # Need to consume the generator to trigger the error
        list(stub.streamingcall("some_method"))
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_stream_raises():
    """Test that stream() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info:
        with stub.stream():
            pass
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_log_stream_raises():
    """Test that log_stream() raises ImportError with driver info."""
    stub = create_stub_client("missing_driver.client.Client")
    with pytest.raises(ImportError) as exc_info:
        with stub.log_stream():
            pass
    assert "missing_driver" in str(exc_info.value)


def test_stub_driver_client_error_message_jumpstarter_driver():
    """Test that error message mentions version mismatch for Jumpstarter drivers."""
    stub = create_stub_client("jumpstarter_driver_xyz.client.XyzClient")
    with pytest.raises(ImportError) as exc_info:
        stub.call("some_method")
    assert "version mismatch" in str(exc_info.value)


def test_stub_driver_client_error_message_third_party():
    """Test that error message includes install instructions for third-party drivers."""
    stub = create_stub_client("custom_driver.client.CustomClient")
    with pytest.raises(ImportError) as exc_info:
        stub.call("some_method")
    assert "pip install custom_driver" in str(exc_info.value)

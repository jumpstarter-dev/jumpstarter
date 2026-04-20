"""Tests for AsyncDriverClient async status methods."""

import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import anyio
import pytest
from grpc import StatusCode
from grpc.aio import AioRpcError

from jumpstarter.common import ExporterStatus, LogSource, Metadata

pytestmark = pytest.mark.anyio


class MockAioRpcError(AioRpcError):
    """Mock gRPC error for testing that properly inherits from AioRpcError."""

    def __init__(self, status_code: StatusCode, message: str = ""):
        self._status_code = status_code
        self._message = message

    def code(self) -> StatusCode:
        return self._status_code

    def details(self) -> str:
        return self._message


def create_mock_rpc_error(code: StatusCode, details: str = "") -> MockAioRpcError:
    """Create a mock AioRpcError with the specified status code."""
    return MockAioRpcError(code, details)


def create_status_response(
    status: ExporterStatus,
    version: int = 1,
    message: str = "",
):
    """Create a mock GetStatusResponse."""
    response = MagicMock()
    response.status = status.to_proto()
    response.status_version = version
    response.message = message
    return response


def create_end_session_response(success: bool = True, message: str = ""):
    """Create a mock EndSessionResponse."""
    response = MagicMock()
    response.success = success
    response.message = message
    return response


@dataclass(kw_only=True)
class MockAsyncDriverClient(Metadata):
    """Minimal mock for testing AsyncDriverClient methods."""
    stub: MagicMock

    def __post_init__(self):
        import logging
        self.logger = logging.getLogger("MockAsyncDriverClient")
        self._status_monitor = None

    async def get_status_async(self) -> ExporterStatus | None:
        """Get the current exporter status."""
        try:
            response = await self.stub.GetStatus(MagicMock())
            return ExporterStatus.from_proto(response.status)
        except AioRpcError as e:
            if e.code() == StatusCode.UNIMPLEMENTED:
                return None
            raise

    async def end_session_async(self) -> bool:
        """End the current session and trigger the afterLease hook."""
        try:
            response = await self.stub.EndSession(MagicMock())
            return response.success
        except AioRpcError as e:
            if e.code() == StatusCode.UNIMPLEMENTED:
                return False
            if e.code() in (StatusCode.UNAVAILABLE, StatusCode.CANCELLED):
                return True
            if e.code() == StatusCode.UNKNOWN and "Stream removed" in str(e.details()):
                return True
            raise

    async def wait_for_hook_status(self, target_status: ExporterStatus, timeout: float = 60.0) -> bool:
        """Wait for exporter to reach a target status using polling."""
        import anyio

        poll_interval = 0.1  # Fast for testing
        elapsed = 0.0

        while elapsed < timeout:
            try:
                status = await self.get_status_async()

                if status is None:
                    return True

                if status == target_status:
                    return True

                if status == ExporterStatus.AFTER_LEASE_HOOK_FAILED:
                    return True

            except AioRpcError:
                return False

            await anyio.sleep(poll_interval)
            elapsed += poll_interval

        return False


class TestAsyncDriverClientGetStatus:
    async def test_get_status_returns_exporter_status(self) -> None:
        """Test that get_status_async() returns the correct ExporterStatus."""
        stub = MagicMock()
        stub.GetStatus = AsyncMock(return_value=create_status_response(ExporterStatus.LEASE_READY))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        status = await client.get_status_async()

        assert status == ExporterStatus.LEASE_READY
        stub.GetStatus.assert_called_once()

    async def test_get_status_unimplemented_returns_none(self) -> None:
        """Test that get_status_async() returns None when UNIMPLEMENTED."""
        stub = MagicMock()
        stub.GetStatus = AsyncMock(side_effect=create_mock_rpc_error(StatusCode.UNIMPLEMENTED))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        status = await client.get_status_async()

        assert status is None

    async def test_get_status_all_statuses(self) -> None:
        """Test that get_status_async() correctly converts all status values."""
        statuses = [
            ExporterStatus.UNSPECIFIED,
            ExporterStatus.OFFLINE,
            ExporterStatus.AVAILABLE,
            ExporterStatus.BEFORE_LEASE_HOOK,
            ExporterStatus.LEASE_READY,
            ExporterStatus.AFTER_LEASE_HOOK,
            ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
            ExporterStatus.AFTER_LEASE_HOOK_FAILED,
        ]

        for expected_status in statuses:
            stub = MagicMock()
            stub.GetStatus = AsyncMock(return_value=create_status_response(expected_status))
            client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

            status = await client.get_status_async()

            assert status == expected_status


class TestAsyncDriverClientEndSession:
    async def test_end_session_success(self) -> None:
        """Test that end_session_async() returns True on success."""
        stub = MagicMock()
        stub.EndSession = AsyncMock(return_value=create_end_session_response(success=True))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.end_session_async()

        assert result is True
        stub.EndSession.assert_called_once()

    async def test_end_session_unimplemented(self) -> None:
        """Test that end_session_async() returns False when UNIMPLEMENTED."""
        stub = MagicMock()
        stub.EndSession = AsyncMock(side_effect=create_mock_rpc_error(StatusCode.UNIMPLEMENTED))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.end_session_async()

        assert result is False

    async def test_end_session_unavailable_treated_as_success(self) -> None:
        """Test that end_session_async() returns True when UNAVAILABLE (lease released)."""
        stub = MagicMock()
        stub.EndSession = AsyncMock(side_effect=create_mock_rpc_error(StatusCode.UNAVAILABLE))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.end_session_async()

        assert result is True

    async def test_end_session_cancelled_treated_as_success(self) -> None:
        """Test that end_session_async() returns True when CANCELLED (connection dropped)."""
        stub = MagicMock()
        stub.EndSession = AsyncMock(side_effect=create_mock_rpc_error(StatusCode.CANCELLED))

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.end_session_async()

        assert result is True

    async def test_end_session_stream_removed_treated_as_success(self) -> None:
        """Test that end_session_async() returns True when stream removed (lease released)."""
        error = create_mock_rpc_error(StatusCode.UNKNOWN, "Stream removed")
        stub = MagicMock()
        stub.EndSession = AsyncMock(side_effect=error)

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.end_session_async()

        assert result is True


class TestAsyncDriverClientWaitForHookStatus:
    async def test_wait_for_hook_status_reaches_target(self) -> None:
        """Test that wait_for_hook_status() returns True when target is reached."""
        stub = MagicMock()
        # First call returns AFTER_LEASE_HOOK, second call returns AVAILABLE
        stub.GetStatus = AsyncMock(
            side_effect=[
                create_status_response(ExporterStatus.AFTER_LEASE_HOOK),
                create_status_response(ExporterStatus.AVAILABLE),
            ]
        )

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.wait_for_hook_status(ExporterStatus.AVAILABLE, timeout=1.0)

        assert result is True

    async def test_wait_for_hook_status_hook_failed(self) -> None:
        """Test that wait_for_hook_status() returns True when hook fails."""
        stub = MagicMock()
        stub.GetStatus = AsyncMock(
            return_value=create_status_response(ExporterStatus.AFTER_LEASE_HOOK_FAILED)
        )

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.wait_for_hook_status(ExporterStatus.AVAILABLE, timeout=1.0)

        assert result is True

    async def test_wait_for_hook_status_timeout(self) -> None:
        """Test that wait_for_hook_status() returns False on timeout."""
        stub = MagicMock()
        # Always return AFTER_LEASE_HOOK (never reaches target)
        stub.GetStatus = AsyncMock(
            return_value=create_status_response(ExporterStatus.AFTER_LEASE_HOOK)
        )

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.wait_for_hook_status(ExporterStatus.AVAILABLE, timeout=0.2)

        assert result is False

    async def test_wait_for_hook_status_connection_error(self) -> None:
        """Test that wait_for_hook_status() returns False on connection error."""
        stub = MagicMock()
        stub.GetStatus = AsyncMock(
            side_effect=create_mock_rpc_error(StatusCode.UNAVAILABLE)
        )

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.wait_for_hook_status(ExporterStatus.AVAILABLE, timeout=1.0)

        assert result is False

    async def test_wait_for_hook_status_unimplemented_returns_true(self) -> None:
        """Test that wait_for_hook_status() returns True when GetStatus not implemented."""
        stub = MagicMock()
        stub.GetStatus = AsyncMock(
            side_effect=create_mock_rpc_error(StatusCode.UNIMPLEMENTED)
        )

        client = MockAsyncDriverClient(uuid=uuid4(), labels={}, stub=stub)

        result = await client.wait_for_hook_status(ExporterStatus.AVAILABLE, timeout=1.0)

        # Should return True (backward compatibility - assume hook complete)
        assert result is True


def create_log_stream_response(message: str, severity: str = "INFO", source=None):
    """Create a mock LogStreamResponse."""
    response = MagicMock()
    response.message = message
    response.severity = severity
    if source is not None:
        response.HasField = lambda field: field == "source"
        response.source = source.to_proto()
    else:
        response.HasField = lambda field: False
        response.source = None
    return response


class LogCapture(logging.Handler):
    """Captures log records for assertion in tests."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


SOURCE_LOGGER_NAMES = [
    "exporter:beforeLease",
    "exporter:afterLease",
    "exporter:driver",
    "exporter:system",
]


def setup_log_stream_client(responses):
    from jumpstarter.client.core import AsyncDriverClient

    all_delivered = anyio.Event()

    async def mock_log_stream(_):
        for r in responses:
            yield r
        all_delivered.set()
        await anyio.sleep_forever()

    stub = MagicMock()
    stub.LogStream = mock_log_stream

    client = MagicMock(spec=AsyncDriverClient)
    client.stub = stub
    client.logger = logging.getLogger("test_log_stream_client")
    client.log_stream_async = AsyncDriverClient.log_stream_async.__get__(client)

    captures = {}
    for name in SOURCE_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        capture = LogCapture()
        logger.addHandler(capture)
        captures[name] = capture

    return client, captures, all_delivered


@pytest.fixture(autouse=True)
def _clean_source_loggers():
    yield
    for name in SOURCE_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.handlers.clear()


class TestLogStreamResponseFactory:
    def test_create_response_without_source(self) -> None:
        response = create_log_stream_response("plain message")
        assert response.message == "plain message"
        assert response.severity == "INFO"
        assert response.HasField("source") is False
        assert response.source is None

    def test_create_response_with_source(self) -> None:
        response = create_log_stream_response(
            "hook message", severity="DEBUG", source=LogSource.BEFORE_LEASE_HOOK
        )
        assert response.message == "hook message"
        assert response.severity == "DEBUG"
        assert response.HasField("source") is True


class TestLogStreamSourceTagPlacement:
    async def test_hook_log_delegates_tagging_to_formatter(self) -> None:
        responses = [
            create_log_stream_response(
                "hook output line",
                severity="INFO",
                source=LogSource.BEFORE_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=True):
            with anyio.fail_after(2):
                await delivered.wait()

        records = captures["exporter:beforeLease"].records
        assert len(records) == 1
        assert records[0].getMessage() == "hook output line"
        assert records[0].name == "exporter:beforeLease"

    async def test_after_lease_hook_log_delegates_tagging_to_formatter(self) -> None:
        responses = [
            create_log_stream_response(
                "cleanup output",
                severity="INFO",
                source=LogSource.AFTER_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=True):
            with anyio.fail_after(2):
                await delivered.wait()

        records = captures["exporter:afterLease"].records
        assert len(records) == 1
        assert records[0].getMessage() == "cleanup output"
        assert records[0].name == "exporter:afterLease"

    async def test_logger_name_carries_source_for_formatter(self) -> None:
        responses = [
            create_log_stream_response(
                "line one",
                severity="INFO",
                source=LogSource.BEFORE_LEASE_HOOK,
            ),
            create_log_stream_response(
                "line two",
                severity="INFO",
                source=LogSource.AFTER_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=True):
            with anyio.fail_after(2):
                await delivered.wait()

        before_records = captures["exporter:beforeLease"].records
        after_records = captures["exporter:afterLease"].records
        assert len(before_records) == 1
        assert before_records[0].name == "exporter:beforeLease"
        assert before_records[0].getMessage() == "line one"
        assert len(after_records) == 1
        assert after_records[0].name == "exporter:afterLease"
        assert after_records[0].getMessage() == "line two"


class TestLogStreamFiltering:
    async def test_show_all_logs_false_filters_system_logs(self) -> None:
        responses = [
            create_log_stream_response(
                "debug system message",
                severity="DEBUG",
                source=LogSource.SYSTEM,
            ),
            create_log_stream_response(
                "hook output line",
                severity="INFO",
                source=LogSource.BEFORE_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=False):
            with anyio.fail_after(2):
                await delivered.wait()

        system_records = captures["exporter:system"].records
        hook_records = captures["exporter:beforeLease"].records
        assert len(system_records) == 0
        assert len(hook_records) == 1

    async def test_show_all_logs_false_shows_hook_logs(self) -> None:
        responses = [
            create_log_stream_response(
                "before hook output",
                severity="INFO",
                source=LogSource.BEFORE_LEASE_HOOK,
            ),
            create_log_stream_response(
                "after hook output",
                severity="INFO",
                source=LogSource.AFTER_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=False):
            with anyio.fail_after(2):
                await delivered.wait()

        before_records = captures["exporter:beforeLease"].records
        after_records = captures["exporter:afterLease"].records
        assert len(before_records) == 1
        assert len(after_records) == 1

    async def test_show_all_logs_true_shows_system_logs(self) -> None:
        responses = [
            create_log_stream_response(
                "system message",
                severity="INFO",
                source=LogSource.SYSTEM,
            ),
            create_log_stream_response(
                "hook output line",
                severity="INFO",
                source=LogSource.BEFORE_LEASE_HOOK,
            ),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=True):
            with anyio.fail_after(2):
                await delivered.wait()

        system_records = captures["exporter:system"].records
        hook_records = captures["exporter:beforeLease"].records
        assert len(system_records) == 1
        assert len(hook_records) == 1

    async def test_log_without_source_routes_to_system(self) -> None:
        responses = [
            create_log_stream_response("no source message"),
        ]

        client, captures, delivered = setup_log_stream_client(responses)

        async with client.log_stream_async(show_all_logs=True):
            with anyio.fail_after(2):
                await delivered.wait()

        system_records = captures["exporter:system"].records
        assert len(system_records) == 1
        assert system_records[0].getMessage() == "no source message"

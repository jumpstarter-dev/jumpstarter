"""Tests for the structured logging setup module."""

import io
import json
import logging
from collections import deque
from unittest.mock import patch

import pytest

from jumpstarter.common import LogSource
from jumpstarter.exporter.logging import LogHandler
from jumpstarter.logging import clear_log_context, set_log_context, setup_logging


class TestSetupLogging:
    def setup_method(self):
        """Reset logging state between tests."""
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def teardown_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def _capture_json_output(self, component: str = "exporter") -> io.StringIO:
        """Setup logging in JSON mode and return the stderr capture."""
        stream = io.StringIO()
        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component=component, log_format="json")
            # Replace the handler's stream with our capture
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream
        return stream

    def test_json_output_has_required_fields(self):
        """JSON log lines must contain ts, level, msg, and component."""
        stream = self._capture_json_output(component="exporter")

        logger = logging.getLogger("test.json_fields")
        logger.info("Test message")

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert "ts" in record
        assert record["level"] == "info"
        assert record["msg"] == "Test message"
        assert record["component"] == "exporter"

    def test_json_timestamp_is_iso8601(self):
        """Timestamp field must be in ISO-8601 format."""
        stream = self._capture_json_output()

        logger = logging.getLogger("test.timestamp")
        logger.info("Timestamp test")

        output = stream.getvalue().strip()
        record = json.loads(output)

        ts = record["ts"]
        # ISO-8601 format: 2026-07-01T10:15:30.123456Z
        assert "T" in ts
        assert ts.endswith("Z")

    def test_json_level_is_lowercase(self):
        """Level field must be lowercase."""
        stream = self._capture_json_output()
        logging.getLogger().setLevel(logging.DEBUG)

        logger = logging.getLogger("test.levels")
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warning("warning msg")
        logger.error("error msg")

        lines = stream.getvalue().strip().split("\n")
        levels = [json.loads(line)["level"] for line in lines]
        assert levels == ["debug", "info", "warning", "error"]

    def test_json_extra_fields_included(self):
        """Extra fields passed via extra= should appear in JSON output."""
        stream = self._capture_json_output()

        logger = logging.getLogger("test.extra")
        logger.info("Operation done", extra={"operation": "flash", "result": "success"})

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert record["operation"] == "flash"
        assert record["result"] == "success"

    def test_component_field_set_correctly(self):
        """Component field should match the value passed to setup_logging."""
        stream = self._capture_json_output(component="router")

        logger = logging.getLogger("test.component")
        logger.info("Test")

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert record["component"] == "router"

    def test_contextvars_fields_injected(self):
        """Fields set via set_log_context should appear in JSON output."""
        stream = self._capture_json_output()

        set_log_context(lease_id="abc-123", client="ci-bot", exporter="lab-01")

        logger = logging.getLogger("test.contextvars")
        logger.info("Lease active")

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert record["lease_id"] == "abc-123"
        assert record["client"] == "ci-bot"
        assert record["exporter"] == "lab-01"

    def test_contextvars_cleared(self):
        """After clear_log_context, fields should not appear."""
        stream = self._capture_json_output()

        set_log_context(lease_id="abc-123")
        clear_log_context()

        logger = logging.getLogger("test.clear")
        logger.info("No context")

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert "lease_id" not in record

    def test_log_handler_compatibility(self):
        """LogHandler (gRPC stream) must still work when structlog is configured."""
        self._capture_json_output()

        queue = deque(maxlen=256)
        log_handler = LogHandler(queue, LogSource.SYSTEM)
        logging.getLogger().addHandler(log_handler)

        logger = logging.getLogger("driver.TestDriver")
        logger.info("Flash started")

        assert len(queue) == 1
        msg = queue[0]
        assert msg.message == "Flash started"
        assert msg.severity == "INFO"
        assert msg.source == LogSource.SYSTEM.value

    def test_log_handler_with_driver_source(self):
        """LogHandler should correctly identify driver source with child handler mapping."""
        self._capture_json_output()

        queue = deque(maxlen=256)
        log_handler = LogHandler(queue, LogSource.SYSTEM)
        log_handler.add_child_handler("driver.", LogSource.DRIVER)
        logging.getLogger().addHandler(log_handler)

        logger = logging.getLogger("driver.QemuFlasher")
        logger.warning("Device timeout")

        assert len(queue) == 1
        msg = queue[0]
        assert msg.message == "Device timeout"
        assert msg.severity == "WARNING"
        assert msg.source == LogSource.DRIVER.value


class TestNamespaceDetection:
    def setup_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def teardown_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_detect_namespace_from_env_var(self):
        """NAMESPACE env var should be detected."""
        from jumpstarter.logging.setup import _detect_namespace

        with patch.dict("os.environ", {"NAMESPACE": "prod-ns"}):
            assert _detect_namespace() == "prod-ns"

    def test_detect_namespace_from_pod_namespace_env(self):
        """POD_NAMESPACE env var should be detected as fallback."""
        from jumpstarter.logging.setup import _detect_namespace

        with patch.dict("os.environ", {"POD_NAMESPACE": "staging"}, clear=True):
            assert _detect_namespace() == "staging"

    def test_detect_namespace_prefers_namespace_over_pod_namespace(self):
        """NAMESPACE should take priority over POD_NAMESPACE."""
        from jumpstarter.logging.setup import _detect_namespace

        with patch.dict("os.environ", {"NAMESPACE": "primary", "POD_NAMESPACE": "secondary"}):
            assert _detect_namespace() == "primary"

    def test_detect_namespace_from_service_account_file(self, tmp_path):
        """Service account namespace file should be used when env vars are absent."""
        from jumpstarter.logging.setup import _detect_namespace

        ns_file = tmp_path / "namespace"
        ns_file.write_text("k8s-namespace\n")

        with patch.dict("os.environ", {}, clear=True):
            with patch("jumpstarter.logging.setup.Path") as mock_path:
                mock_instance = mock_path.return_value
                mock_instance.exists.return_value = True
                mock_instance.read_text.return_value = "k8s-namespace\n"
                assert _detect_namespace() == "k8s-namespace"

    def test_detect_namespace_returns_none_when_not_in_k8s(self):
        """Should return None when no env vars and no service account file."""
        from jumpstarter.logging.setup import _detect_namespace

        with patch.dict("os.environ", {}, clear=True):
            with patch("jumpstarter.logging.setup.Path") as mock_path:
                mock_instance = mock_path.return_value
                mock_instance.exists.return_value = False
                assert _detect_namespace() is None

    def test_setup_logging_binds_namespace_when_available(self):
        """Namespace should appear in log output when detected."""
        stream = io.StringIO()
        with patch.dict("os.environ", {"NAMESPACE": "test-ns"}):
            with patch("jumpstarter.logging.setup.sys.stderr", stream):
                setup_logging(component="exporter", log_format="json")
                root = logging.getLogger()
                for handler in root.handlers:
                    if isinstance(handler, logging.StreamHandler):
                        handler.stream = stream

        logger = logging.getLogger("test.namespace_bind")
        logger.info("With namespace")

        output = stream.getvalue().strip()
        record = json.loads(output)
        assert record["namespace"] == "test-ns"

    def test_setup_logging_no_namespace_when_not_in_k8s(self):
        """Namespace should be absent from logs when not detectable."""
        stream = io.StringIO()
        with patch.dict("os.environ", {}, clear=True):
            with patch("jumpstarter.logging.setup.Path") as mock_path:
                mock_instance = mock_path.return_value
                mock_instance.exists.return_value = False
                with patch("jumpstarter.logging.setup.sys.stderr", stream):
                    setup_logging(component="exporter", log_format="json")
                    root = logging.getLogger()
                    for handler in root.handlers:
                        if isinstance(handler, logging.StreamHandler):
                            handler.stream = stream

        logger = logging.getLogger("test.no_namespace")
        logger.info("Without namespace")

        output = stream.getvalue().strip()
        record = json.loads(output)
        assert "namespace" not in record


class TestContextFieldsEndToEnd:
    """Integration test: set_log_context with spec.context keys appear in both JSON output and LogHandler."""

    def setup_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def teardown_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_spec_context_fields_in_json_output(self):
        """spec.context keys (build_id, image_digest) appear in JSON output."""
        stream = io.StringIO()
        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="json")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        set_log_context(
            lease_id="lease-abc",
            exporter="lab-01",
            build_id="nightly-42",
            image_digest="sha256:deadbeef",
        )

        logger = logging.getLogger("test.context_e2e")
        logger.info("Flash started", extra={"operation": "flash", "driver_type": "storage"})

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert record["lease_id"] == "lease-abc"
        assert record["exporter"] == "lab-01"
        assert record["build_id"] == "nightly-42"
        assert record["image_digest"] == "sha256:deadbeef"
        assert record["operation"] == "flash"
        assert record["driver_type"] == "storage"
        assert record["component"] == "exporter"

    def test_spec_context_and_error_type_in_json_output(self):
        """error_type and result fields coexist with spec.context in JSON."""
        stream = io.StringIO()
        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="json")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        set_log_context(lease_id="lease-xyz", exporter="lab-02", build_id="ci-99")

        logger = logging.getLogger("test.error_e2e")
        logger.warning(
            "Operation failed",
            extra={
                "operation": "power_on",
                "driver_type": "power",
                "result": "failure",
                "error_type": "device_error",
            },
        )

        output = stream.getvalue().strip()
        record = json.loads(output)

        assert record["error_type"] == "device_error"
        assert record["result"] == "failure"
        assert record["build_id"] == "ci-99"
        assert record["lease_id"] == "lease-xyz"

    def test_log_handler_captures_context_fields(self):
        """LogHandler structured_fields include context vars set via set_log_context."""
        stream = io.StringIO()
        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="json")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        queue = deque(maxlen=256)
        log_handler = LogHandler(queue, LogSource.DRIVER)
        logging.getLogger().addHandler(log_handler)

        set_log_context(lease_id="lease-dual", exporter="dual-exp")

        logger = logging.getLogger("driver.TestDual")
        logger.info(
            "Op done",
            extra={
                "operation": "flash",
                "driver_type": "storage",
                "result": "success",
                "lease_id": "lease-dual",
                "exporter": "dual-exp",
            },
        )

        assert len(queue) == 1
        msg = queue[0]
        assert msg.operation == "flash"
        assert msg.driver_type == "storage"
        assert msg.structured_fields["result"] == "success"
        assert msg.structured_fields["lease_id"] == "lease-dual"
        assert msg.structured_fields["exporter"] == "dual-exp"
        assert msg.HasField("timestamp")


class TestAutoDetect:
    def setup_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def teardown_method(self):
        clear_log_context()
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_auto_json_when_not_tty(self):
        """Auto mode should produce JSON when stderr is not a TTY."""
        stream = io.StringIO()
        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            # StringIO.isatty() returns False
            setup_logging(component="exporter", log_format="auto")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        logger = logging.getLogger("test.auto_json")
        logger.info("Should be JSON")

        output = stream.getvalue().strip()
        record = json.loads(output)
        assert record["msg"] == "Should be JSON"
        assert record["component"] == "exporter"

    def test_auto_text_when_tty(self):
        """Auto mode should produce human-readable text when stderr is a TTY."""
        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[assignment]

        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="auto")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        logger = logging.getLogger("test.auto_text")
        logger.info("Should be text")

        output = stream.getvalue().strip()
        # Text mode should NOT be valid JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)
        # But should contain the message
        assert "Should be text" in output

    def test_json_format_override(self):
        """Explicit json format should always produce JSON even with TTY."""
        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[assignment]

        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="json")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        logger = logging.getLogger("test.json_override")
        logger.info("Forced JSON")

        output = stream.getvalue().strip()
        record = json.loads(output)
        assert record["msg"] == "Forced JSON"

    def test_text_format_override(self):
        """Explicit text format should produce text even without TTY."""
        stream = io.StringIO()
        # StringIO.isatty() returns False by default

        with patch("jumpstarter.logging.setup.sys.stderr", stream):
            setup_logging(component="exporter", log_format="text")
            root = logging.getLogger()
            for handler in root.handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.stream = stream

        logger = logging.getLogger("test.text_override")
        logger.info("Forced text")

        output = stream.getvalue().strip()
        with pytest.raises(json.JSONDecodeError):
            json.loads(output)
        assert "Forced text" in output

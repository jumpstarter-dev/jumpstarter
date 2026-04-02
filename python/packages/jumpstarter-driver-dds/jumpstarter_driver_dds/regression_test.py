"""Regression tests to prevent recurrence of review-finding categories.

These tests guard structural invariants that are easy to break when
adding new backends, driver classes, CLI commands, or Pydantic models.
"""

from __future__ import annotations

import dataclasses as dc
import pathlib

import pytest

from .common import (
    DdsPublishResult,
    DdsReadResult,
    DdsSample,
    DdsTopicQos,
)
from .driver import Dds, MockDds, _make_idl_type, _validate_field_names
from jumpstarter.common.utils import serve
from jumpstarter.driver.decorators import (
    MARKER_DRIVERCALL,
    MARKER_MAGIC,
    MARKER_STREAMING_DRIVERCALL,
)

# =============================================================================
# 1. Backend parity: identical operations must produce identical results
# =============================================================================


class TestBackendParity:
    """Run against ALL backends via the ``any_backend`` fixture."""

    def test_partial_publish_fills_defaults(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["x", "y"], DdsTopicQos())
        any_backend.publish("t", {"x": "1"})
        result = any_backend.read("t", 10)
        assert result.samples[0].data == {"x": "1", "y": ""}

    def test_empty_publish_fills_all_defaults(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["a", "b"], DdsTopicQos())
        any_backend.publish("t", {})
        result = any_backend.read("t", 10)
        assert result.samples[0].data == {"a": "", "b": ""}

    def test_unknown_field_raises_valueerror(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["x"], DdsTopicQos())
        with pytest.raises(ValueError, match="Unknown field"):
            any_backend.publish("t", {"z": "bad"})

    def test_publish_nonexistent_topic_raises_valueerror(self, any_backend):
        any_backend.connect()
        with pytest.raises(ValueError, match="not registered"):
            any_backend.publish("nope", {"x": "1"})

    def test_read_nonexistent_topic_raises_valueerror(self, any_backend):
        any_backend.connect()
        with pytest.raises(ValueError, match="not registered"):
            any_backend.read("nope", 10)

    def test_duplicate_topic_raises_valueerror(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["x"], DdsTopicQos())
        with pytest.raises(ValueError, match="already exists"):
            any_backend.create_topic("t", ["y"], DdsTopicQos())

    def test_history_depth_trim(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["v"], DdsTopicQos(history_depth=3))
        for i in range(5):
            any_backend.publish("t", {"v": str(i)})
        result = any_backend.read("t", 10)
        assert result.sample_count == 3
        assert result.samples[0].data["v"] == "2"

    def test_read_consumes_buffer(self, any_backend):
        any_backend.connect()
        any_backend.create_topic("t", ["v"], DdsTopicQos())
        any_backend.publish("t", {"v": "1"})
        first = any_backend.read("t", 10)
        assert first.sample_count == 1
        second = any_backend.read("t", 10)
        assert second.sample_count == 0

    def test_operations_before_connect_raise_runtimeerror(self, any_backend):
        with pytest.raises(RuntimeError, match="Not connected"):
            any_backend.create_topic("t", ["f"], DdsTopicQos())

    def test_double_connect_raises_runtimeerror(self, any_backend):
        any_backend.connect()
        with pytest.raises(RuntimeError, match="Already connected"):
            any_backend.connect()


# =============================================================================
# 2. Driver interface parity: Dds and MockDds must expose the same surface
# =============================================================================


class TestDriverInterfaceParity:
    def test_export_methods_match(self):
        def _exported(cls):
            """Detect exported methods using the official jumpstarter markers."""
            result: set[str] = set()
            for name in vars(cls):
                if name.startswith("_"):
                    continue
                method = getattr(cls, name, None)
                if not callable(method):
                    continue
                if (
                    getattr(method, MARKER_DRIVERCALL, None) == MARKER_MAGIC
                    or getattr(method, MARKER_STREAMING_DRIVERCALL, None) == MARKER_MAGIC
                ):
                    result.add(name)
            return result

        dds_methods = _exported(Dds)
        mock_methods = _exported(MockDds)
        assert dds_methods == mock_methods, (
            f"Export mismatch: Dds has {dds_methods - mock_methods}, MockDds has {mock_methods - dds_methods}"
        )

    def test_shared_config_fields_present(self):
        shared = {"domain_id", "default_reliability", "default_durability", "default_history_depth"}
        dds_fields = {f.name for f in dc.fields(Dds) if not f.name.startswith("_")}
        mock_fields = {f.name for f in dc.fields(MockDds) if not f.name.startswith("_")}
        assert shared.issubset(dds_fields), f"Dds missing: {shared - dds_fields}"
        assert shared.issubset(mock_fields), f"MockDds missing: {shared - mock_fields}"

    def test_both_override_close(self):
        assert "close" in vars(Dds), "Dds must override close()"
        assert "close" in vars(MockDds), "MockDds must override close()"

    def test_both_have_default_qos(self):
        assert hasattr(Dds, "_default_qos")
        assert hasattr(MockDds, "_default_qos")


# =============================================================================
# 3. CLI completeness: every public API method has a CLI command
# =============================================================================


class TestCliCompleteness:
    def test_all_expected_commands_registered(self):
        with serve(MockDds()) as client:
            cli = client.cli()
            expected = {
                "connect",
                "disconnect",
                "topics",
                "info",
                "create-topic",
                "publish",
                "read",
                "monitor",
            }
            assert expected.issubset(set(cli.commands)), f"Missing CLI commands: {expected - set(cli.commands)}"


# =============================================================================
# 4. Model invariants
# =============================================================================


class TestModelInvariants:
    def test_read_result_rejects_count_mismatch(self):
        with pytest.raises(ValueError, match="sample_count"):
            DdsReadResult(
                topic_name="t",
                samples=[DdsSample(topic_name="t", data={"k": "v"})],
                sample_count=0,
            )

    def test_read_result_accepts_consistent_count(self):
        r = DdsReadResult(
            topic_name="t",
            samples=[DdsSample(topic_name="t", data={"k": "v"})],
            sample_count=1,
        )
        assert r.sample_count == 1

    def test_publish_result_has_no_success_field(self):
        assert "success" not in DdsPublishResult.model_fields, (
            "DdsPublishResult should not have a 'success' field; failures are signalled via exceptions"
        )


# =============================================================================
# 5. _make_idl_type name collision guard
# =============================================================================


class TestIdlTypeCollisions:
    def test_similar_names_produce_distinct_types(self):
        t1 = _make_idl_type("sensor/temp", ["v"])
        t2 = _make_idl_type("sensor-temp", ["v"])
        assert t1.__name__ != t2.__name__

    def test_numeric_prefix_produces_valid_identifier(self):
        t = _make_idl_type("123-topic", ["v"])
        assert t.__name__.isidentifier()

    def test_slash_dot_dash_all_handled(self):
        for name in ["a/b", "a.b", "a-b", "a/b.c-d"]:
            t = _make_idl_type(name, ["f"])
            assert t.__name__.isidentifier(), f"Invalid identifier for topic '{name}'"


# =============================================================================
# 6. Field name validation
# =============================================================================


class TestFieldNameValidation:
    def test_invalid_identifier_rejected(self):
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            _validate_field_names(["123bad"])

    def test_keyword_rejected(self):
        with pytest.raises(ValueError, match="Python keyword"):
            _validate_field_names(["class"])

    def test_duplicate_rejected(self):
        with pytest.raises(ValueError, match="Duplicate"):
            _validate_field_names(["x", "x"])

    def test_valid_fields_pass(self):
        _validate_field_names(["speed", "heading", "timestamp"])

    def test_backend_rejects_invalid_fields(self, any_backend):
        any_backend.connect()
        with pytest.raises(ValueError):
            any_backend.create_topic("t", ["123bad"], DdsTopicQos())

    def test_backend_rejects_duplicate_fields(self, any_backend):
        any_backend.connect()
        with pytest.raises(ValueError, match="Duplicate"):
            any_backend.create_topic("t", ["x", "x"], DdsTopicQos())


# =============================================================================
# 7. history_depth validation
# =============================================================================


class TestHistoryDepthValidation:
    def test_zero_depth_rejected(self):
        with pytest.raises(ValueError):
            DdsTopicQos(history_depth=0)

    def test_negative_depth_rejected(self):
        with pytest.raises(ValueError):
            DdsTopicQos(history_depth=-1)

    def test_depth_one_accepted(self):
        qos = DdsTopicQos(history_depth=1)
        assert qos.history_depth == 1


# =============================================================================
# 8. Project conventions
# =============================================================================


class TestProjectConventions:
    def test_no_asyncio_imports_in_production_code(self):
        pkg = pathlib.Path(__file__).parent
        for py_file in pkg.glob("*.py"):
            if py_file.name.endswith("_test.py"):
                continue
            content = py_file.read_text()
            assert "import asyncio" not in content, (
                f"{py_file.name} imports asyncio; use anyio instead (project convention)"
            )

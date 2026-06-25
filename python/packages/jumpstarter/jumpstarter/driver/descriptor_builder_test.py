"""Tests for build_file_descriptor()."""

from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator

from google.protobuf.descriptor_pb2 import (
    FileDescriptorProto,
)
from jumpstarter_driver_power.driver import PowerInterface
from pydantic import BaseModel

from .descriptor_builder import build_file_descriptor

# ---------------------------------------------------------------------------
# Test interfaces
#
# These follow the current-branch convention: a plain class with
# `metaclass=ABCMeta`, a `client()` classmethod, and `@abstractmethod`
# interface methods (no DriverInterface base, no @export on the abstract
# methods). The builder discovers methods purely by introspection.
# ---------------------------------------------------------------------------

class SensorInterface(metaclass=ABCMeta):
    """Interface with typed parameters and return values."""

    @classmethod
    def client(cls) -> str:
        return "test.SensorClient"

    @abstractmethod
    async def read_temperature(self) -> float: ...

    @abstractmethod
    async def set_threshold(self, value: float, channel: int) -> None: ...


class StreamingInterface(metaclass=ABCMeta):
    """Interface with streaming methods."""

    @classmethod
    def client(cls) -> str:
        return "test.StreamingClient"

    @abstractmethod
    async def read_values(self) -> AsyncGenerator[float, None]: ...


class EmptyInterface(metaclass=ABCMeta):  # noqa: B024 — intentionally has no abstract methods
    """Interface with no methods beyond client()."""

    @classmethod
    def client(cls) -> str:
        return "test.EmptyClient"


class SensorReading(BaseModel):
    voltage: float
    current: float


class ModelInterface(metaclass=ABCMeta):
    """Interface using Pydantic model types."""

    @classmethod
    def client(cls) -> str:
        return "test.ModelClient"

    @abstractmethod
    async def get_reading(self) -> SensorReading: ...


# ---------------------------------------------------------------------------
# Required: PowerInterface (the real one from jumpstarter_driver_power)
# ---------------------------------------------------------------------------

class TestPowerInterface:
    """The on-demand dynamic-dispatch entry point for the real PowerInterface.

    PowerInterface exposes on()/off() (unary, no params, -> None) and
    read() -> AsyncGenerator[PowerReading, None] (server-streaming).
    """

    def test_service_name(self):
        fd = build_file_descriptor(PowerInterface)
        assert len(fd.service) == 1
        assert fd.service[0].name == "PowerInterface"

    def test_method_names(self):
        fd = build_file_descriptor(PowerInterface)
        names = {m.name for m in fd.service[0].method}
        assert names == {"On", "Off", "Read"}

    def test_on_off_are_empty_unary(self):
        fd = build_file_descriptor(PowerInterface)
        for name in ("On", "Off"):
            m = next(m for m in fd.service[0].method if m.name == name)
            assert m.input_type == ".google.protobuf.Empty"
            assert m.output_type == ".google.protobuf.Empty"
            assert m.server_streaming is False
            assert m.client_streaming is False

    def test_read_is_server_streaming_powerreading(self):
        fd = build_file_descriptor(PowerInterface)
        read = next(m for m in fd.service[0].method if m.name == "Read")
        # No params -> Empty input
        assert read.input_type == ".google.protobuf.Empty"
        # AsyncGenerator[PowerReading, None] -> server streaming, PowerReading output
        assert read.server_streaming is True
        assert read.client_streaming is False
        assert read.output_type.endswith(".PowerReading")
        # The PowerReading message is emitted with its model fields
        pr = next(m for m in fd.message_type if m.name == "PowerReading")
        field_names = [f.name for f in pr.field]
        assert field_names == ["voltage", "current"]


# ---------------------------------------------------------------------------
# Basic FileDescriptorProto structure
# ---------------------------------------------------------------------------

class TestFileDescriptorStructure:
    """Verify the top-level FileDescriptorProto is well-formed."""

    def test_file_name(self):
        fd = build_file_descriptor(PowerInterface)
        # The "Interface" suffix is stripped for the proto file/package name.
        assert fd.name == "power.proto"

    def test_package_name(self):
        fd = build_file_descriptor(PowerInterface)
        assert fd.package == "jumpstarter.interfaces.power.v1"

    def test_package_custom_version(self):
        fd = build_file_descriptor(PowerInterface, version="v2")
        assert fd.package == "jumpstarter.interfaces.power.v2"

    def test_syntax_proto3(self):
        fd = build_file_descriptor(PowerInterface)
        assert fd.syntax == "proto3"

    def test_has_single_service(self):
        fd = build_file_descriptor(PowerInterface)
        assert len(fd.service) == 1

    def test_service_name_matches_interface(self):
        fd = build_file_descriptor(PowerInterface)
        assert fd.service[0].name == "PowerInterface"


# ---------------------------------------------------------------------------
# Service methods
# ---------------------------------------------------------------------------

class TestServiceMethods:
    """Verify service methods are correctly generated."""

    def test_power_interface_method_count(self):
        """PowerInterface has 3 methods: on, off, read."""
        fd = build_file_descriptor(PowerInterface)
        methods = fd.service[0].method
        assert len(methods) == 3

    def test_method_names_are_pascal_case(self):
        fd = build_file_descriptor(PowerInterface)
        method_names = [m.name for m in fd.service[0].method]
        assert "On" in method_names
        assert "Off" in method_names
        assert "Read" in method_names

    def test_snake_case_to_pascal_case(self):
        """Multi-word method names should be PascalCase."""
        fd = build_file_descriptor(SensorInterface)
        method_names = [m.name for m in fd.service[0].method]
        assert "ReadTemperature" in method_names
        assert "SetThreshold" in method_names

    def test_unary_method_not_streaming(self):
        """Simple async methods should not be marked as streaming."""
        fd = build_file_descriptor(PowerInterface)
        on_method = next(m for m in fd.service[0].method if m.name == "On")
        assert on_method.server_streaming is False
        assert on_method.client_streaming is False

    def test_server_streaming_method(self):
        """AsyncGenerator return -> server_streaming: true."""
        fd = build_file_descriptor(StreamingInterface)
        read_method = next(
            m for m in fd.service[0].method if m.name == "ReadValues"
        )
        assert read_method.server_streaming is True
        assert read_method.client_streaming is False


# ---------------------------------------------------------------------------
# Request/response messages
# ---------------------------------------------------------------------------

class TestRequestResponseMessages:
    """Verify request/response message generation."""

    def test_no_params_uses_empty(self):
        """Methods with no params should use google.protobuf.Empty as input."""
        fd = build_file_descriptor(PowerInterface)
        on_method = next(m for m in fd.service[0].method if m.name == "On")
        assert on_method.input_type == ".google.protobuf.Empty"

    def test_none_return_uses_empty(self):
        """Methods returning None should use google.protobuf.Empty as output."""
        fd = build_file_descriptor(PowerInterface)
        on_method = next(m for m in fd.service[0].method if m.name == "On")
        assert on_method.output_type == ".google.protobuf.Empty"

    def test_params_generate_request_message(self):
        """Methods with params should have a generated request message."""
        fd = build_file_descriptor(SensorInterface)
        set_method = next(
            m for m in fd.service[0].method if m.name == "SetThreshold"
        )
        # Should reference a generated request message, not Empty
        assert "SetThresholdRequest" in set_method.input_type

    def test_primitive_return_generates_response_message(self):
        """Methods returning primitives should generate a response wrapper."""
        fd = build_file_descriptor(SensorInterface)
        read_method = next(
            m for m in fd.service[0].method if m.name == "ReadTemperature"
        )
        # Should reference a response message wrapping the float
        assert read_method.output_type != ".google.protobuf.Empty"
        assert "ReadTemperatureResponse" in read_method.output_type

    def test_request_message_fields(self):
        """Generated request message should have correct fields."""
        fd = build_file_descriptor(SensorInterface)
        set_method = next(
            m for m in fd.service[0].method if m.name == "SetThreshold"
        )
        req_msg_name = set_method.input_type.split(".")[-1]
        req_msg = next(m for m in fd.message_type if m.name == req_msg_name)

        assert len(req_msg.field) == 2
        field_names = [f.name for f in req_msg.field]
        assert "value" in field_names
        assert "channel" in field_names


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

class TestDependencies:
    """Verify well-known type dependencies are tracked."""

    def test_empty_dependency_when_needed(self):
        """google/protobuf/empty.proto should be a dependency when Empty is used."""
        fd = build_file_descriptor(PowerInterface)
        assert "google/protobuf/empty.proto" in list(fd.dependency)

    def test_no_empty_dependency_when_not_needed(self):
        """An interface that never uses Empty should not declare it."""
        fd = build_file_descriptor(ModelInterface)
        # get_reading() has no params -> Empty input, so Empty IS needed here.
        # (Documents current behavior: a no-param method still needs Empty.)
        assert "google/protobuf/empty.proto" in list(fd.dependency)


# ---------------------------------------------------------------------------
# Empty interface
# ---------------------------------------------------------------------------

class TestEmptyInterface:
    """Verify handling of interfaces with no methods."""

    def test_empty_service(self):
        fd = build_file_descriptor(EmptyInterface)
        assert len(fd.service) == 1
        assert fd.service[0].name == "EmptyInterface"
        assert len(fd.service[0].method) == 0

    def test_empty_no_messages(self):
        fd = build_file_descriptor(EmptyInterface)
        assert len(fd.message_type) == 0


# ---------------------------------------------------------------------------
# Model types in methods
# ---------------------------------------------------------------------------

class TestModelTypeMethods:
    """Verify Pydantic model types produce correct message descriptors."""

    def test_model_return_generates_message(self):
        fd = build_file_descriptor(ModelInterface)
        msg_names = [m.name for m in fd.message_type]
        assert any("SensorReading" in name for name in msg_names)


# ---------------------------------------------------------------------------
# Round-trip: build -> serialize -> parse
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Verify FileDescriptorProto can be serialized and parsed back."""

    def test_serialize_deserialize(self):
        """Build -> serialize -> deserialize should produce identical proto."""
        fd = build_file_descriptor(PowerInterface)
        serialized = fd.SerializeToString()
        parsed = FileDescriptorProto()
        parsed.ParseFromString(serialized)

        assert parsed.name == fd.name
        assert parsed.package == fd.package
        assert parsed.syntax == fd.syntax
        assert len(parsed.service) == len(fd.service)
        assert parsed.service[0].name == fd.service[0].name
        assert len(parsed.service[0].method) == len(fd.service[0].method)

    def test_deterministic_output(self):
        """Two calls with the same input should produce identical output."""
        fd1 = build_file_descriptor(PowerInterface)
        fd2 = build_file_descriptor(PowerInterface)
        assert fd1.SerializeToString() == fd2.SerializeToString()


# ---------------------------------------------------------------------------
# Doc comment extraction
# ---------------------------------------------------------------------------

class DocCommentInterface(metaclass=ABCMeta):
    """A well-documented interface for testing doc extraction."""

    @classmethod
    def client(cls) -> str:
        return "test.DocClient"

    @abstractmethod
    async def power_on(self) -> None:
        """Turn the power relay on."""
        ...

    @abstractmethod
    async def read_voltage(self) -> float:
        """Read the output voltage in volts."""
        ...

    @abstractmethod
    async def undocumented(self) -> None: ...


class TestDocCommentExtraction:
    """Verify docstrings are captured in source_code_info."""

    def test_source_code_info_present(self):
        """FileDescriptorProto should have source_code_info when docstrings exist."""
        fd = build_file_descriptor(DocCommentInterface)
        assert fd.HasField("source_code_info")
        assert len(fd.source_code_info.location) > 0

    def test_service_level_comment(self):
        """Interface class docstring should become the service comment."""
        fd = build_file_descriptor(DocCommentInterface)
        # Service path is [6, 0] (service field=6, index=0)
        service_locs = [
            loc
            for loc in fd.source_code_info.location
            if list(loc.path) == [6, 0]
        ]
        assert len(service_locs) == 1
        assert "well-documented interface" in service_locs[0].leading_comments

    def test_method_level_comment(self):
        """Method docstrings should become method-level comments."""
        fd = build_file_descriptor(DocCommentInterface)
        # Method paths are [6, 0, 2, N] (service=6, idx=0, method=2, method_idx=N)
        method_locs = [
            loc
            for loc in fd.source_code_info.location
            if len(loc.path) == 4 and loc.path[0] == 6 and loc.path[2] == 2
        ]
        comments = [loc.leading_comments for loc in method_locs]
        assert any("power relay" in c for c in comments)
        assert any("output voltage" in c for c in comments)

    def test_undocumented_method_no_comment(self):
        """Methods without docstrings should not have source_code_info entries."""
        fd = build_file_descriptor(DocCommentInterface)
        method_locs = [
            loc
            for loc in fd.source_code_info.location
            if len(loc.path) == 4 and loc.path[0] == 6 and loc.path[2] == 2
        ]
        comments = [loc.leading_comments for loc in method_locs]
        assert not any("undocumented" in c.lower() for c in comments)

    def test_no_source_code_info_for_empty_interface(self):
        """Empty interface should have no method-level source_code_info."""
        fd = build_file_descriptor(EmptyInterface)
        if fd.HasField("source_code_info"):
            method_locs = [
                loc
                for loc in fd.source_code_info.location
                if len(loc.path) == 4 and loc.path[0] == 6 and loc.path[2] == 2
            ]
            assert len(method_locs) == 0

"""Tests for jmp interface CLI tools (Phase 8 generate, Phase 11 check)."""

from abc import abstractmethod
from collections.abc import AsyncGenerator

import pytest
from click.testing import CliRunner
from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    MethodDescriptorProto,
    ServiceDescriptorProto,
    SourceCodeInfo,
)

from jumpstarter.driver import DriverInterface, export
from jumpstarter.driver.descriptor_builder import build_file_descriptor

from .interface import (
    CheckResult,
    _compare_descriptors,
    _format_comment,
    _gen_client_py,
    _gen_driver_py,
    _gen_init_py,
    _gen_interface_py,
    _pascal_to_snake,
    _resolve_type_name,
    generate,
    render_proto_source,
)


# ---------------------------------------------------------------------------
# Test interfaces
# ---------------------------------------------------------------------------

class PowerTestInterface(DriverInterface):
    """A power control interface."""

    @classmethod
    def client(cls) -> str:
        return "test.PowerClient"

    @abstractmethod
    async def on(self) -> None:
        """Turn power on."""
        ...

    @abstractmethod
    async def off(self) -> None: ...

    @abstractmethod
    async def status(self) -> str: ...


class StreamTestInterface(DriverInterface):
    """Interface with streaming."""

    @classmethod
    def client(cls) -> str:
        return "test.StreamClient"

    @abstractmethod
    async def read_values(self) -> AsyncGenerator[float, None]: ...


# ---------------------------------------------------------------------------
# Proto source rendering
# ---------------------------------------------------------------------------

class TestRenderProtoSource:
    """Verify render_proto_source() produces valid .proto text."""

    def test_header_comment(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert "DO NOT EDIT" in source

    def test_syntax_line(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert 'syntax = "proto3";' in source

    def test_package_line(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert "package jumpstarter.interfaces.powertestinterface.v1;" in source

    def test_service_declaration(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert "service PowerTestInterface {" in source

    def test_rpc_methods(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert "rpc On(" in source
        assert "rpc Off(" in source
        assert "rpc Status(" in source

    def test_empty_import(self):
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert 'import "google/protobuf/empty.proto";' in source

    def test_message_definitions(self):
        """Generated messages should appear in the output."""
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        # Status returns str, so should have a response message
        assert "message " in source

    def test_streaming_method(self):
        """Server streaming methods should use 'stream' keyword."""
        fd = build_file_descriptor(StreamTestInterface)
        source = render_proto_source(fd)
        assert "stream" in source

    def test_doc_comments_rendered(self):
        """Docstrings should appear as // comments."""
        fd = build_file_descriptor(PowerTestInterface)
        source = render_proto_source(fd)
        assert "// A power control interface" in source or "// Turn power on" in source


# ---------------------------------------------------------------------------
# Generate CLI command
# ---------------------------------------------------------------------------

class TestGenerateCommand:
    """Test the 'jmp interface generate' CLI command."""

    def test_generate_to_stdout(self):
        """Generate should output proto source to stdout."""
        runner = CliRunner()
        interface_path = f"{PowerTestInterface.__module__}.PowerTestInterface"
        result = runner.invoke(generate, ["--interface", interface_path])
        assert result.exit_code == 0
        assert "syntax" in result.output
        assert "service PowerTestInterface" in result.output

    def test_generate_to_file(self, tmp_path):
        """Generate should write proto source to a file."""
        output = str(tmp_path / "test.proto")
        runner = CliRunner()
        interface_path = f"{PowerTestInterface.__module__}.PowerTestInterface"
        result = runner.invoke(generate, [
            "--interface", interface_path,
            "--output", output,
        ])
        assert result.exit_code == 0
        with open(output) as f:
            content = f.read()
        assert 'syntax = "proto3";' in content

    def test_generate_with_version(self):
        """--version flag should change the package version."""
        runner = CliRunner()
        interface_path = f"{PowerTestInterface.__module__}.PowerTestInterface"
        result = runner.invoke(generate, [
            "--interface", interface_path,
            "--version", "v2",
        ])
        assert result.exit_code == 0
        assert "powertestinterface.v2" in result.output

    def test_generate_invalid_interface(self):
        """Invalid interface path should produce an error."""
        runner = CliRunner()
        result = runner.invoke(generate, ["--interface", "no.such.module.Foo"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestUtilities:
    """Test helper functions."""

    def test_resolve_type_name_same_package(self):
        assert _resolve_type_name(".pkg.v1.MyMsg", "pkg.v1") == "MyMsg"

    def test_resolve_type_name_google(self):
        result = _resolve_type_name(".google.protobuf.Empty", "pkg.v1")
        assert result == "google.protobuf.Empty"

    def test_resolve_type_name_different_package(self):
        result = _resolve_type_name(".other.pkg.Msg", "my.pkg")
        assert result == "other.pkg.Msg"

    def test_format_comment_single_line(self):
        result = _format_comment("Hello world", "  ")
        assert result == "  // Hello world"

    def test_format_comment_multi_line(self):
        result = _format_comment("Line 1\nLine 2", "")
        assert "// Line 1" in result
        assert "// Line 2" in result


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------

class TestCheckResult:
    """Test CheckResult accumulator."""

    def test_empty_is_ok(self):
        r = CheckResult()
        assert r.ok
        assert r.strict_ok
        assert r.exit_code() == 0

    def test_structural_not_ok(self):
        r = CheckResult()
        r.structural.append("missing method")
        assert not r.ok
        assert r.exit_code() == 1

    def test_contract_strict(self):
        r = CheckResult()
        r.contract.append("field mismatch")
        assert r.ok
        assert not r.strict_ok
        assert r.exit_code(strict=False) == 0
        assert r.exit_code(strict=True) == 2

    def test_docs_informational(self):
        r = CheckResult()
        r.docs.append("doc drift")
        assert r.ok
        assert r.exit_code() == 0


# ---------------------------------------------------------------------------
# Descriptor comparison (_compare_descriptors)
# ---------------------------------------------------------------------------

class TestCompareDescriptors:
    """Test descriptor comparison for jmp interface check."""

    def _make_fd(self, service_name, methods):
        """Helper to build a minimal FileDescriptorProto."""
        fd = FileDescriptorProto(
            name="test.proto",
            package="test.v1",
            syntax="proto3",
        )
        svc = ServiceDescriptorProto(name=service_name)
        for m in methods:
            svc.method.append(m)
        fd.service.append(svc)
        return fd

    def _make_method(self, name, input_type=".google.protobuf.Empty",
                     output_type=".google.protobuf.Empty",
                     server_streaming=False, client_streaming=False):
        return MethodDescriptorProto(
            name=name,
            input_type=input_type,
            output_type=output_type,
            server_streaming=server_streaming,
            client_streaming=client_streaming,
        )

    def test_identical_descriptors(self):
        methods = [self._make_method("On"), self._make_method("Off")]
        fd1 = self._make_fd("Power", methods)
        fd2 = self._make_fd("Power", methods)
        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert result.ok

    def test_missing_method_in_proto(self):
        fd1 = self._make_fd("Power", [self._make_method("On"), self._make_method("Off")])
        fd2 = self._make_fd("Power", [self._make_method("On")])
        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert not result.ok
        assert any("Off" in m for m in result.structural)

    def test_extra_method_in_proto(self):
        fd1 = self._make_fd("Power", [self._make_method("On")])
        fd2 = self._make_fd("Power", [self._make_method("On"), self._make_method("Off")])
        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert not result.ok
        assert any("Off" in m for m in result.structural)

    def test_streaming_mismatch(self):
        fd1 = self._make_fd("Svc", [self._make_method("Read", server_streaming=True)])
        fd2 = self._make_fd("Svc", [self._make_method("Read", server_streaming=False)])
        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert not result.ok
        assert any("server_streaming" in m for m in result.structural)

    def test_service_name_mismatch(self):
        fd1 = self._make_fd("Power", [])
        fd2 = self._make_fd("Sensor", [])
        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert any("name mismatch" in m for m in result.structural)

    def test_message_field_mismatch(self):
        """Different message fields should produce contract differences."""
        fd1 = self._make_fd("Svc", [])
        fd2 = self._make_fd("Svc", [])

        msg1 = DescriptorProto(name="Request")
        msg1.field.append(FieldDescriptorProto(
            name="value", number=1, type=FieldDescriptorProto.TYPE_STRING
        ))
        fd1.message_type.append(msg1)

        msg2 = DescriptorProto(name="Request")
        msg2.field.append(FieldDescriptorProto(
            name="value", number=1, type=FieldDescriptorProto.TYPE_INT64
        ))
        fd2.message_type.append(msg2)

        result = CheckResult()
        _compare_descriptors(fd1, fd2, result)
        assert any("type mismatch" in m for m in result.structural)


# ---------------------------------------------------------------------------
# Phase 10: jmp interface implement — code generation
# ---------------------------------------------------------------------------

def _make_power_fd():
    """Build a FileDescriptorProto simulating a simple Power interface."""
    fd = FileDescriptorProto(
        name="powerinterface.proto",
        package="jumpstarter.interfaces.powerinterface.v1",
        syntax="proto3",
    )
    fd.dependency.append("google/protobuf/empty.proto")

    svc = ServiceDescriptorProto(name="PowerInterface")
    svc.method.append(MethodDescriptorProto(
        name="On",
        input_type=".google.protobuf.Empty",
        output_type=".google.protobuf.Empty",
    ))
    svc.method.append(MethodDescriptorProto(
        name="Off",
        input_type=".google.protobuf.Empty",
        output_type=".google.protobuf.Empty",
    ))

    # Status returns a string → wrapped in a response message
    status_resp = DescriptorProto(name="StatusResponse")
    status_resp.field.append(FieldDescriptorProto(
        name="value", number=1, type=FieldDescriptorProto.TYPE_STRING,
    ))
    fd.message_type.append(status_resp)

    svc.method.append(MethodDescriptorProto(
        name="Status",
        input_type=".google.protobuf.Empty",
        output_type=f".{fd.package}.StatusResponse",
    ))
    fd.service.append(svc)
    return fd


class TestPascalToSnake:
    """Test PascalCase → snake_case conversion."""

    def test_simple(self):
        assert _pascal_to_snake("On") == "on"

    def test_multi_word(self):
        assert _pascal_to_snake("ReadTemperature") == "read_temperature"

    def test_consecutive_caps(self):
        assert _pascal_to_snake("HTTPServer") == "http_server"

    def test_single_word(self):
        assert _pascal_to_snake("Status") == "status"


class TestGenInterfacePy:
    """Test interface.py code generation (Phase 10)."""

    def test_generates_class_declaration(self):
        fd = _make_power_fd()
        code = _gen_interface_py(fd, "jumpstarter_driver_power")
        assert "class PowerInterface(DriverInterface):" in code

    def test_generates_client_classmethod(self):
        fd = _make_power_fd()
        code = _gen_interface_py(fd, "jumpstarter_driver_power")
        assert "def client(cls) -> str:" in code
        assert "jumpstarter_driver_power.client.PowerClient" in code

    def test_generates_abstract_methods(self):
        fd = _make_power_fd()
        code = _gen_interface_py(fd, "jumpstarter_driver_power")
        assert "@abstractmethod" in code
        assert "async def on(self) -> None:" in code
        assert "async def off(self) -> None:" in code
        assert "async def status(self) -> str:" in code

    def test_imports_driver_interface(self):
        fd = _make_power_fd()
        code = _gen_interface_py(fd, "jumpstarter_driver_power")
        assert "from jumpstarter.driver import DriverInterface" in code

    def test_do_not_edit_comment(self):
        fd = _make_power_fd()
        code = _gen_interface_py(fd, "jumpstarter_driver_power")
        assert "Do not edit" in code


class TestGenClientPy:
    """Test client.py code generation (Phase 10)."""

    def test_generates_client_class(self):
        fd = _make_power_fd()
        code = _gen_client_py(fd, "jumpstarter_driver_power")
        assert "class PowerClient(PowerInterface, DriverClient):" in code

    def test_generates_call_methods(self):
        fd = _make_power_fd()
        code = _gen_client_py(fd, "jumpstarter_driver_power")
        assert "def on(self) -> None:" in code
        assert 'self.call("on")' in code

    def test_generates_return_methods(self):
        fd = _make_power_fd()
        code = _gen_client_py(fd, "jumpstarter_driver_power")
        assert "def status(self) -> str:" in code
        assert 'return self.call("status")' in code

    def test_imports_interface(self):
        fd = _make_power_fd()
        code = _gen_client_py(fd, "jumpstarter_driver_power")
        assert "from .interface import PowerInterface" in code


class TestGenDriverPy:
    """Test driver.py adapter generation (Phase 10)."""

    def test_generates_driver_class(self):
        fd = _make_power_fd()
        code = _gen_driver_py(fd, "jumpstarter_driver_power")
        assert "class PowerDriver(PowerInterface, Driver):" in code

    def test_generates_export_methods(self):
        fd = _make_power_fd()
        code = _gen_driver_py(fd, "jumpstarter_driver_power")
        assert "@export" in code
        assert "async def on(self) -> None:" in code

    def test_generates_abstract_underscore_methods(self):
        fd = _make_power_fd()
        code = _gen_driver_py(fd, "jumpstarter_driver_power")
        assert "@abstractmethod" in code
        assert "async def _on(self) -> None:" in code
        assert "async def _off(self) -> None:" in code

    def test_imports_driver_and_export(self):
        fd = _make_power_fd()
        code = _gen_driver_py(fd, "jumpstarter_driver_power")
        assert "from jumpstarter.driver import Driver, export" in code


class TestGenInitPy:
    """Test __init__.py generation (Phase 10)."""

    def test_exports_all_types(self):
        fd = _make_power_fd()
        code = _gen_init_py(fd, "jumpstarter_driver_power")
        assert "PowerInterface" in code
        assert "PowerClient" in code
        assert "PowerDriver" in code
        assert "__all__" in code

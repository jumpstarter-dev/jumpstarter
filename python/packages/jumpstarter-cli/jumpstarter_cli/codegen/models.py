"""Shared data models for the jmp codegen pipeline.

These models represent the resolved inputs that per-language generators consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class Optionality(Enum):
    REQUIRED = auto()
    OPTIONAL = auto()


@dataclass
class InterfaceMethod:
    """A single RPC method on a DriverInterface."""

    name: str
    """Proto method name (PascalCase, e.g. 'On', 'Read')."""

    input_type: str
    """Fully-qualified proto input type (e.g. 'google.protobuf.Empty')."""

    output_type: str
    """Fully-qualified proto output type."""

    client_streaming: bool = False
    server_streaming: bool = False
    stream_constructor: bool = False
    """True for @exportstream methods that use RouterService.Stream."""

    doc_comment: str | None = None
    """Leading comment from the .proto file."""


@dataclass
class MessageField:
    """A field within a proto message."""

    name: str
    number: int
    type_name: str
    """Scalar type name (e.g. 'double', 'string') or fully-qualified message type."""
    is_repeated: bool = False
    is_optional: bool = False
    is_message: bool = False
    is_enum: bool = False


@dataclass
class ProtoMessage:
    """A proto message definition."""

    name: str
    """Short name (e.g. 'PowerReading')."""
    full_name: str
    """Fully-qualified name (e.g. 'jumpstarter.interfaces.power.v1.PowerReading')."""
    fields: list[MessageField] = field(default_factory=list)
    doc_comment: str | None = None
    is_data_model: bool = False
    """True if this is a user-facing data model (not just a request/response wrapper)."""


@dataclass
class ProtoEnum:
    """A proto enum definition."""

    name: str
    full_name: str
    values: list[tuple[str, int]] = field(default_factory=list)
    """(name, number) pairs."""


@dataclass
class DriverInterfaceRef:
    """Resolved reference to a DriverInterface with its proto information."""

    name: str
    """Accessor name in the ExporterClass (e.g. 'power', 'serial')."""

    interface_ref: str
    """DriverInterface CRD name (e.g. 'dev-jumpstarter-power-v1')."""

    proto_package: str
    """Proto package (e.g. 'jumpstarter.interfaces.power.v1')."""

    service_name: str
    """gRPC service name (e.g. 'PowerInterface')."""

    proto_file_path: str | None = None
    """Path to the .proto file on disk, if available."""

    proto_source: str | None = None
    """Raw .proto file content for bundling with generated code (e.g. for TypeScript proto-loader)."""

    optionality: Optionality = Optionality.REQUIRED

    methods: list[InterfaceMethod] = field(default_factory=list)
    messages: list[ProtoMessage] = field(default_factory=list)
    enums: list[ProtoEnum] = field(default_factory=list)

    doc_comment: str | None = None
    """Service-level doc comment from the .proto file."""

    # Language-specific implementation hints from the DriverInterface CRD
    drivers: dict[str, DriverImplementationHint] = field(default_factory=dict)
    """Language → implementation hint (package name, client class, etc.)."""


@dataclass
class DriverImplementationHint:
    """Per-language implementation info from the DriverInterface CRD."""

    language: str
    package: str
    version: str | None = None
    client_class: str | None = None
    driver_classes: list[str] = field(default_factory=list)


@dataclass
class ExporterClassSpec:
    """Parsed ExporterClass with resolved interface references."""

    name: str
    """ExporterClass name (e.g. 'dev-board')."""

    extends: str | None = None
    """Parent ExporterClass name, if any."""

    interfaces: list[DriverInterfaceRef] = field(default_factory=list)
    """All resolved interface references (required + optional)."""

    @property
    def required_interfaces(self) -> list[DriverInterfaceRef]:
        return [i for i in self.interfaces if i.optionality == Optionality.REQUIRED]

    @property
    def optional_interfaces(self) -> list[DriverInterfaceRef]:
        return [i for i in self.interfaces if i.optionality == Optionality.OPTIONAL]


@dataclass
class CodegenContext:
    """Everything a language generator needs to produce output.

    This is the single input object passed to LanguageGenerator methods.
    """

    exporter_class: ExporterClassSpec
    """The resolved ExporterClass specification."""

    language: str
    """Target language (e.g. 'python', 'java', 'typescript', 'rust')."""

    output_dir: str
    """Root output directory for generated files."""

    generate_test_fixtures: bool = False
    """Whether to generate test framework fixtures."""

    package_name: str | None = None
    """Override package name for the generated output."""

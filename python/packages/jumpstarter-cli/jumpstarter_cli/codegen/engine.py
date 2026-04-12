"""Shared code generator engine for jmp codegen.

Resolves ExporterClass definitions to DriverInterface references,
parses .proto files to extract service/method/message information,
and provides a LanguageGenerator protocol for per-language codegen.
"""

from __future__ import annotations

import base64
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import click
from google.protobuf.descriptor_pb2 import (
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
    MethodDescriptorProto,
)

from .models import (
    CodegenContext,
    DriverImplementationHint,
    DriverInterfaceRef,
    ExporterClassSpec,
    InterfaceMethod,
    MessageField,
    Optionality,
    ProtoEnum,
    ProtoMessage,
)

if TYPE_CHECKING:
    pass

# FileDescriptorProto path constants for source_code_info lookups
_FDP_MESSAGE_TYPE = 4
_FDP_SERVICE = 6
_SDP_METHOD = 2

# Scalar type name mapping
_SCALAR_TYPE_NAMES = {
    FieldDescriptorProto.TYPE_DOUBLE: "double",
    FieldDescriptorProto.TYPE_FLOAT: "float",
    FieldDescriptorProto.TYPE_INT64: "int64",
    FieldDescriptorProto.TYPE_UINT64: "uint64",
    FieldDescriptorProto.TYPE_INT32: "int32",
    FieldDescriptorProto.TYPE_FIXED64: "fixed64",
    FieldDescriptorProto.TYPE_FIXED32: "fixed32",
    FieldDescriptorProto.TYPE_BOOL: "bool",
    FieldDescriptorProto.TYPE_STRING: "string",
    FieldDescriptorProto.TYPE_BYTES: "bytes",
    FieldDescriptorProto.TYPE_UINT32: "uint32",
    FieldDescriptorProto.TYPE_SFIXED32: "sfixed32",
    FieldDescriptorProto.TYPE_SFIXED64: "sfixed64",
    FieldDescriptorProto.TYPE_SINT32: "sint32",
    FieldDescriptorProto.TYPE_SINT64: "sint64",
}


# ---------------------------------------------------------------------------
# Proto parsing
# ---------------------------------------------------------------------------


def parse_proto_file(
    proto_path: str,
    include_paths: list[str] | None = None,
) -> FileDescriptorProto:
    """Parse a .proto file into a FileDescriptorProto using grpcio-tools.

    This reuses the same approach as proto.py — grpcio-tools bundles protoc
    and well-known types, so no external binary is required.
    """
    import grpc_tools
    from grpc_tools import protoc as grpc_protoc

    proto_path = os.path.abspath(proto_path)
    proto_dir = os.path.dirname(proto_path)
    proto_name = os.path.basename(proto_path)

    paths = [proto_dir]
    if include_paths:
        paths.extend(include_paths)

    wkt_path = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")
    if os.path.isdir(wkt_path):
        paths.append(wkt_path)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        args = ["grpc_tools.protoc"]
        for p in paths:
            args.append(f"-I{p}")
        args.extend([
            f"--descriptor_set_out={tmp_path}",
            "--include_source_info",
            proto_name,
        ])

        rc = grpc_protoc.main(args)
        if rc != 0:
            raise click.ClickException(f"protoc failed with exit code {rc}")

        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise click.ClickException("protoc produced empty output")

        fds = FileDescriptorSet.FromString(data)
        if not fds.file:
            raise click.ClickException("protoc produced empty descriptor set")

        return fds.file[-1]
    finally:
        os.unlink(tmp_path)


def parse_descriptor_bytes(descriptor_bytes: bytes) -> FileDescriptorProto:
    """Parse a serialized FileDescriptorProto (e.g. from a DriverInterface CRD descriptor field)."""
    fd = FileDescriptorProto()
    fd.ParseFromString(descriptor_bytes)
    return fd


def _get_comment(fd: FileDescriptorProto, path: list[int]) -> str | None:
    """Look up leading comment from source_code_info for the given path."""
    if not fd.HasField("source_code_info"):
        return None
    for loc in fd.source_code_info.location:
        if list(loc.path) == path:
            text = loc.leading_comments.rstrip("\n")
            if text:
                return text
    return None


def _resolve_type_name(type_name: str) -> str:
    """Strip leading dot from fully-qualified type names."""
    if type_name.startswith("."):
        return type_name[1:]
    return type_name


def _extract_methods(fd: FileDescriptorProto) -> list[InterfaceMethod]:
    """Extract InterfaceMethod objects from the first service in a FileDescriptorProto."""
    if not fd.service:
        return []

    service = fd.service[0]
    methods = []
    for m_idx, method in enumerate(service.method):
        # Detect @exportstream methods: bidi streaming or client streaming
        # indicates RouterService.Stream usage
        is_stream_constructor = method.client_streaming

        methods.append(InterfaceMethod(
            name=method.name,
            input_type=_resolve_type_name(method.input_type),
            output_type=_resolve_type_name(method.output_type),
            client_streaming=method.client_streaming,
            server_streaming=method.server_streaming,
            stream_constructor=is_stream_constructor,
            doc_comment=_get_comment(fd, [_FDP_SERVICE, 0, _SDP_METHOD, m_idx]),
        ))

    return methods


def _extract_messages(fd: FileDescriptorProto) -> list[ProtoMessage]:
    """Extract ProtoMessage objects from a FileDescriptorProto."""
    package = fd.package
    service = fd.service[0] if fd.service else None

    # Determine which message names are used as RPC request/response types
    rpc_types: set[str] = set()
    if service:
        for method in service.method:
            for type_name in (method.input_type, method.output_type):
                rpc_types.add(type_name.rsplit(".", 1)[-1])

    messages = []
    for msg_idx, msg in enumerate(fd.message_type):
        full_name = f"{package}.{msg.name}" if package else msg.name

        fields = []
        for f in msg.field:
            if f.type in (FieldDescriptorProto.TYPE_MESSAGE, FieldDescriptorProto.TYPE_ENUM):
                type_name = _resolve_type_name(f.type_name)
            else:
                type_name = _SCALAR_TYPE_NAMES.get(f.type, "unknown")

            fields.append(MessageField(
                name=f.name,
                number=f.number,
                type_name=type_name,
                is_repeated=f.label == FieldDescriptorProto.LABEL_REPEATED,
                is_optional=f.proto3_optional,
                is_message=f.type == FieldDescriptorProto.TYPE_MESSAGE,
                is_enum=f.type == FieldDescriptorProto.TYPE_ENUM,
            ))

        # A message is a data model if it's not solely used as a request/response wrapper
        is_data = _is_data_message(msg, rpc_types, list(service.method) if service else [])

        messages.append(ProtoMessage(
            name=msg.name,
            full_name=full_name,
            fields=fields,
            doc_comment=_get_comment(fd, [_FDP_MESSAGE_TYPE, msg_idx]),
            is_data_model=is_data,
        ))

    return messages


def _is_data_message(
    msg, rpc_types: set[str], service_methods: list[MethodDescriptorProto],
) -> bool:
    """Determine if a message is a data model vs a request/response wrapper.

    Same logic as proto.py's _is_data_message.
    """
    if msg.name not in rpc_types:
        return True
    if any(
        m.output_type.endswith(f".{msg.name}") and m.server_streaming
        for m in service_methods
    ):
        return True
    if len(msg.field) > 1 or (len(msg.field) == 1 and msg.field[0].name != "value"):
        return True
    return False


def _extract_enums(fd: FileDescriptorProto) -> list[ProtoEnum]:
    """Extract ProtoEnum objects from a FileDescriptorProto."""
    package = fd.package
    enums = []
    for enum_desc in fd.enum_type:
        full_name = f"{package}.{enum_desc.name}" if package else enum_desc.name
        enums.append(ProtoEnum(
            name=enum_desc.name,
            full_name=full_name,
            values=[(v.name, v.number) for v in enum_desc.value],
        ))
    # Also collect nested enums from messages
    for msg in fd.message_type:
        for enum_desc in msg.enum_type:
            full_name = f"{package}.{msg.name}.{enum_desc.name}" if package else f"{msg.name}.{enum_desc.name}"
            enums.append(ProtoEnum(
                name=enum_desc.name,
                full_name=full_name,
                values=[(v.name, v.number) for v in enum_desc.value],
            ))
    return enums


def resolve_interface_from_proto(
    proto_path: str,
    name: str,
    interface_ref: str,
    optionality: Optionality,
    include_paths: list[str] | None = None,
) -> DriverInterfaceRef:
    """Resolve a single DriverInterface from a .proto file on disk."""
    fd = parse_proto_file(proto_path, include_paths)

    if not fd.service:
        raise click.ClickException(f"No service found in {proto_path}")

    service = fd.service[0]

    # Read proto source for bundling with generated code (e.g. TypeScript proto-loader)
    proto_source = None
    try:
        with open(proto_path) as f:
            proto_source = f.read()
    except OSError:
        pass

    return DriverInterfaceRef(
        name=name,
        interface_ref=interface_ref,
        proto_package=fd.package,
        service_name=service.name,
        proto_file_path=os.path.abspath(proto_path),
        proto_source=proto_source,
        optionality=optionality,
        methods=_extract_methods(fd),
        messages=_extract_messages(fd),
        enums=_extract_enums(fd),
        doc_comment=_get_comment(fd, [_FDP_SERVICE, 0]),
    )


def resolve_interface_from_descriptor(
    descriptor_b64: str,
    name: str,
    interface_ref: str,
    proto_package: str,
    optionality: Optionality,
    drivers: list[dict] | None = None,
) -> DriverInterfaceRef:
    """Resolve a DriverInterface from a base64-encoded FileDescriptorProto.

    This is used when reading DriverInterface CRDs from the cluster or YAML,
    where the proto descriptor is embedded as a base64 string.
    """
    descriptor_bytes = base64.b64decode(descriptor_b64)
    fd = parse_descriptor_bytes(descriptor_bytes)

    if not fd.service:
        raise click.ClickException(
            f"No service found in descriptor for interface '{interface_ref}'"
        )

    service = fd.service[0]

    driver_hints = {}
    if drivers:
        for drv in drivers:
            lang = drv.get("language", "")
            driver_hints[lang] = DriverImplementationHint(
                language=lang,
                package=drv.get("package", ""),
                version=drv.get("version"),
                client_class=drv.get("clientClass"),
                driver_classes=drv.get("driverClasses", []),
            )

    # Reconstruct proto source from descriptor for bundling (e.g. TypeScript proto-loader)
    proto_source = None
    try:
        from jumpstarter_cli.proto import render_proto_source
        proto_source = render_proto_source(fd)
    except Exception:
        pass

    return DriverInterfaceRef(
        name=name,
        interface_ref=interface_ref,
        proto_package=proto_package,
        service_name=service.name,
        proto_source=proto_source,
        optionality=optionality,
        methods=_extract_methods(fd),
        messages=_extract_messages(fd),
        enums=_extract_enums(fd),
        doc_comment=_get_comment(fd, [_FDP_SERVICE, 0]),
        drivers=driver_hints,
    )


# ---------------------------------------------------------------------------
# ExporterClass resolution
# ---------------------------------------------------------------------------


def resolve_exporter_class_from_file(
    yaml_path: str,
    proto_search_paths: list[str] | None = None,
    driver_interface_dir: str | None = None,
) -> ExporterClassSpec:
    """Resolve an ExporterClass from a local YAML file.

    For each interface requirement, looks up the DriverInterface definition to
    get the proto package and descriptor. DriverInterface definitions can be:
    1. Co-located YAML files in driver_interface_dir
    2. Resolved from proto files found in proto_search_paths
    """
    import yaml

    yaml_path = os.path.abspath(yaml_path)

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    if data.get("kind") != "ExporterClass":
        raise click.ClickException(
            f"Expected kind: ExporterClass in {yaml_path}, got: {data.get('kind')}"
        )

    metadata = data.get("metadata", {})
    spec = data.get("spec", {})

    ec = ExporterClassSpec(
        name=metadata.get("name", os.path.basename(yaml_path).rsplit(".", 1)[0]),
        extends=spec.get("extends"),
    )

    interfaces = spec.get("interfaces", [])
    if not interfaces:
        return ec

    # Build a lookup of DriverInterface YAML files if a directory is provided
    di_lookup: dict[str, dict] = {}
    if driver_interface_dir and os.path.isdir(driver_interface_dir):
        di_lookup = _load_driver_interface_dir(driver_interface_dir)

    for iface in interfaces:
        iface_name = iface.get("name", "")
        iface_ref = iface.get("interfaceRef", "")
        required = iface.get("required", True)
        optionality = Optionality.REQUIRED if required else Optionality.OPTIONAL

        ref = _resolve_single_interface(
            iface_name, iface_ref, optionality,
            di_lookup, proto_search_paths,
        )
        ec.interfaces.append(ref)

    return ec


def _load_driver_interface_dir(dir_path: str) -> dict[str, dict]:
    """Load all DriverInterface YAML files from a directory, keyed by metadata.name."""
    import yaml

    lookup: dict[str, dict] = {}
    for fname in os.listdir(dir_path):
        if not fname.endswith((".yaml", ".yml")):
            continue
        fpath = os.path.join(dir_path, fname)
        try:
            with open(fpath) as f:
                data = yaml.safe_load(f)
            if data and data.get("kind") == "DriverInterface":
                name = data.get("metadata", {}).get("name", "")
                if name:
                    lookup[name] = data
        except Exception:
            pass  # Skip unparseable files
    return lookup


def _resolve_single_interface(
    name: str,
    interface_ref: str,
    optionality: Optionality,
    di_lookup: dict[str, dict],
    proto_search_paths: list[str] | None,
) -> DriverInterfaceRef:
    """Resolve a single interface requirement to a DriverInterfaceRef.

    Resolution order:
    1. DriverInterface YAML (from di_lookup) with embedded descriptor
    2. Proto file discovery from proto_search_paths
    """
    # Try DriverInterface YAML with embedded descriptor
    if interface_ref in di_lookup:
        di_data = di_lookup[interface_ref]
        spec = di_data.get("spec", {})
        proto_spec = spec.get("proto", {})
        proto_package = proto_spec.get("package", "")
        descriptor_b64 = proto_spec.get("descriptor")
        drivers = spec.get("drivers")

        if descriptor_b64:
            return resolve_interface_from_descriptor(
                descriptor_b64=descriptor_b64,
                name=name,
                interface_ref=interface_ref,
                proto_package=proto_package,
                optionality=optionality,
                drivers=drivers,
            )

    # Try proto file discovery
    if proto_search_paths:
        proto_path = _find_proto_file(interface_ref, proto_search_paths)
        if proto_path:
            return resolve_interface_from_proto(
                proto_path=proto_path,
                name=name,
                interface_ref=interface_ref,
                optionality=optionality,
            )

    raise click.ClickException(
        f"Could not resolve DriverInterface '{interface_ref}' for accessor '{name}'. "
        f"Provide a --driver-interface-dir with DriverInterface YAML files, "
        f"or --proto-search-path to discover .proto files."
    )


def _find_proto_file(interface_ref: str, search_paths: list[str]) -> str | None:
    """Find a .proto file matching an interface ref in search paths.

    Naming convention: interface ref 'dev-jumpstarter-power-v1' maps to
    proto package 'jumpstarter.interfaces.power.v1', which lives in
    a file like proto/power/v1/power.proto.
    """
    # Parse interface ref: dev-jumpstarter-{name}-{version}
    # to derive the expected proto directory structure
    parts = interface_ref.split("-")
    if len(parts) >= 3 and parts[0] == "dev" and parts[1] == "jumpstarter":
        # e.g., dev-jumpstarter-power-v1 → name=power, version=v1
        version = parts[-1] if parts[-1].startswith("v") else "v1"
        iface_parts = parts[2:-1] if parts[-1].startswith("v") else parts[2:]
        iface_name = "_".join(iface_parts)

        for search_path in search_paths:
            # Look for proto/{iface_name}/{version}/{iface_name}.proto
            candidate = os.path.join(search_path, iface_name, version, f"{iface_name}.proto")
            if os.path.isfile(candidate):
                return candidate

            # Also search recursively under driver packages
            for root, _dirs, files in os.walk(search_path):
                for fname in files:
                    if fname == f"{iface_name}.proto":
                        fpath = os.path.join(root, fname)
                        # Verify the path includes the version
                        if version in fpath:
                            return fpath

    return None


async def resolve_exporter_class_from_cluster(
    name: str,
) -> ExporterClassSpec:
    """Resolve an ExporterClass from the Kubernetes cluster.

    Uses the admin API to fetch the ExporterClass and its referenced
    DriverInterfaces, extracting proto descriptors from the CRDs.
    """
    from jumpstarter_kubernetes.driverinterfaces import DriverInterfacesV1Alpha1Api
    from jumpstarter_kubernetes.exporterclasses import ExporterClassesV1Alpha1Api
    from jumpstarter_kubernetes.util import get_client_config

    namespace, k8s_config = await get_client_config()

    async with ExporterClassesV1Alpha1Api(namespace=namespace, config=k8s_config) as ec_api:
        ec = await ec_api.get_exporter_class(name)

    if not ec.spec or not ec.spec.interfaces:
        return ExporterClassSpec(
            name=ec.metadata.name,
            extends=ec.spec.extends if ec.spec else None,
        )

    async with DriverInterfacesV1Alpha1Api(namespace=namespace, config=k8s_config) as di_api:
        result = ExporterClassSpec(
            name=ec.metadata.name,
            extends=ec.spec.extends,
        )

        for iface_req in ec.spec.interfaces:
            optionality = Optionality.REQUIRED if iface_req.required else Optionality.OPTIONAL

            # Fetch the DriverInterface CRD to get proto descriptor
            di = await di_api.get_driver_interface(iface_req.interface_ref)

            if di.spec and di.spec.proto.descriptor:
                drivers = None
                if di.spec.drivers:
                    drivers = [
                        {
                            "language": d.language,
                            "package": d.package,
                            "version": d.version,
                            "clientClass": d.client_class,
                            "driverClasses": d.driver_classes,
                        }
                        for d in di.spec.drivers
                    ]

                ref = resolve_interface_from_descriptor(
                    descriptor_b64=di.spec.proto.descriptor,
                    name=iface_req.name,
                    interface_ref=iface_req.interface_ref,
                    proto_package=di.spec.proto.package,
                    optionality=optionality,
                    drivers=drivers,
                )
            else:
                # No descriptor — create a minimal ref
                ref = DriverInterfaceRef(
                    name=iface_req.name,
                    interface_ref=iface_req.interface_ref,
                    proto_package=di.spec.proto.package if di.spec else "",
                    service_name="",
                    optionality=optionality,
                )

            result.interfaces.append(ref)

    return result


# ---------------------------------------------------------------------------
# Language generator protocol
# ---------------------------------------------------------------------------


class LanguageGenerator(ABC):
    """Abstract base class for per-language code generators.

    Each target language (Python, Java, TypeScript, Rust) implements this
    interface to produce language-specific output from a CodegenContext.
    """

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Return the language identifier (e.g. 'python', 'java')."""

    @abstractmethod
    def generate_interface_client(
        self, ctx: CodegenContext, interface: DriverInterfaceRef,
    ) -> dict[str, str]:
        """Generate per-interface typed client code.

        Returns a mapping of relative file paths → file contents.
        For example, Java might return:
          {"dev/jumpstarter/interfaces/power/v1/PowerClient.java": "..."}
        """

    @abstractmethod
    def generate_device_wrapper(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate the ExporterClass device wrapper.

        Returns a mapping of relative file paths → file contents.
        """

    @abstractmethod
    def generate_test_fixture(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate test framework fixtures.

        Returns a mapping of relative file paths → file contents.
        Only called when ctx.generate_test_fixtures is True.
        """

    def generate_package_metadata(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate package metadata files (pom.xml, package.json, etc.).

        Optional — override in subclasses that need it.
        Returns a mapping of relative file paths → file contents.
        """
        return {}

    def generate_all(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate all output files for the given context.

        Returns a mapping of relative file paths → file contents.
        """
        output: dict[str, str] = {}

        # Per-interface clients
        for interface in ctx.exporter_class.interfaces:
            output.update(self.generate_interface_client(ctx, interface))

        # Device wrapper
        output.update(self.generate_device_wrapper(ctx))

        # Test fixtures (if requested)
        if ctx.generate_test_fixtures:
            output.update(self.generate_test_fixture(ctx))

        # Package metadata
        output.update(self.generate_package_metadata(ctx))

        return output


def write_generated_files(output: dict[str, str], output_dir: str) -> list[str]:
    """Write generated files to disk and return list of written paths."""
    written = []
    for rel_path, content in sorted(output.items()):
        abs_path = os.path.join(output_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        written.append(abs_path)
    return written


# ---------------------------------------------------------------------------
# Language generator registry
# ---------------------------------------------------------------------------

_generators: dict[str, type[LanguageGenerator]] = {}


def register_language(name: str, generator_cls: type[LanguageGenerator]) -> None:
    """Register a language generator."""
    _generators[name] = generator_cls


def get_language_generator(name: str) -> LanguageGenerator:
    """Get an instance of a language generator by name."""
    if name not in _generators:
        _try_load_language(name)
    if name not in _generators:
        available = ", ".join(sorted(_generators.keys())) or "(none)"
        raise click.ClickException(
            f"Unknown language '{name}'. Available: {available}"
        )
    return _generators[name]()


def _try_load_language(name: str) -> None:
    """Try to import a language generator module by convention."""
    try:
        import importlib
        importlib.import_module(f"jumpstarter_cli.codegen.languages.{name}")
    except ImportError:
        pass


def available_languages() -> list[str]:
    """Return list of available language names."""
    # Try loading all known languages
    for lang in ("python", "java", "typescript", "rust"):
        _try_load_language(lang)
    return sorted(_generators.keys())

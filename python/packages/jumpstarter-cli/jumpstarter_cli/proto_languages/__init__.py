"""Per-language driver client package generators for `jmp proto generate --language`.

Each generator takes a FileDescriptorProto and produces a complete, publishable
driver client package for its target language — including build configuration,
proto compilation setup, typed client class, and @exportstream port-forward wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from google.protobuf.descriptor_pb2 import FileDescriptorProto

# Type for a generator function:
#   (fd, output_dir, output_package, proto_source) -> dict[relative_path, content]
DriverPackageGenerator = Callable[
    ["FileDescriptorProto", str, str, str],
    dict[str, str],
]

_registry: dict[str, DriverPackageGenerator] = {}


def register_language(name: str, generator: DriverPackageGenerator) -> None:
    _registry[name] = generator


def get_language_generator(name: str) -> DriverPackageGenerator | None:
    # Lazy-import generators on first access
    if not _registry:
        try:
            import jumpstarter_cli.proto_languages.java  # noqa: F401
        except ImportError:
            pass
        try:
            import jumpstarter_cli.proto_languages.typescript  # noqa: F401
        except ImportError:
            pass
        try:
            import jumpstarter_cli.proto_languages.rust  # noqa: F401
        except ImportError:
            pass
    return _registry.get(name)

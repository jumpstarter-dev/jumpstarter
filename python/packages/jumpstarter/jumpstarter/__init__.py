"""Jumpstarter — the thin Python driver/client library.

The client/exporter/controller runtime, the wire protocol and the value codec all
live in the Rust core (the ``jumpstarter_core`` extension). This package provides
only the driver-author surface: the ``Driver``/``DriverClient`` base classes and
the ``@export``/``@exportstream`` decorators.
"""

# Replace UniFFI's per-byte RustBuffer writer with a bulk memmove on import (resource/flash
# throughput: ~9.5 -> 80+ MiB/s). See jumpstarter._uniffi_patch.
from jumpstarter import _uniffi_patch as _uniffi_patch  # noqa: F401

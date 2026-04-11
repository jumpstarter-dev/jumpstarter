"""
Client-side native gRPC routing for transparent DriverCall upgrade.

When a driver reports native_services, calls can be routed through
native gRPC stubs instead of the generic DriverCall dispatch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_UUID_METADATA_KEY = "x-jumpstarter-driver-uuid"


@dataclass(frozen=True)
class NativeClientAdapterInfo:
    """Metadata about a native client adapter for a gRPC service."""

    service_name: str
    stub_class: type
    call_handlers: dict[str, Callable]
    streaming_call_handlers: dict[str, Callable]


_native_client_adapters: dict[str, NativeClientAdapterInfo] = {}


def register_native_client_adapter(
    service_name: str,
    stub_class: type,
    call_handlers: dict[str, Callable],
    streaming_call_handlers: dict[str, Callable] | None = None,
) -> None:
    """Register a native client adapter for a gRPC service.

    Args:
        service_name: Fully-qualified gRPC service name.
        stub_class: The generated gRPC stub class.
        call_handlers: Mapping of method name → callable(stub, uuid, *args).
            Each callable makes the native gRPC call and returns the result.
        streaming_call_handlers: Mapping of method name → async generator factory.
            Each callable makes a native streaming gRPC call.
    """
    _native_client_adapters[service_name] = NativeClientAdapterInfo(
        service_name=service_name,
        stub_class=stub_class,
        call_handlers=call_handlers,
        streaming_call_handlers=streaming_call_handlers or {},
    )
    logger.debug("Registered native client adapter for %s", service_name)


def get_native_client_adapter(service_name: str) -> NativeClientAdapterInfo | None:
    """Look up the native client adapter for a gRPC service name."""
    return _native_client_adapters.get(service_name)

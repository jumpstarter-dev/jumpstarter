"""
DriverRegistry: routes native gRPC calls to the correct driver instance.

Maps (service_name, uuid) pairs to driver instances. Used by generated
servicer adapters to resolve the target driver from gRPC call metadata.

Also provides the servicer adapter registry: a global mapping from
DriverInterface classes to their generated gRPC servicer adapters.

See JEP-0003 for design details.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import grpc

logger = logging.getLogger(__name__)

_UUID_METADATA_KEY = "x-jumpstarter-driver-uuid"


# ── Servicer adapter registry ─────────────────────────────────────────


@dataclass(frozen=True)
class ServicerAdapterInfo:
    """Metadata about a generated servicer adapter for a DriverInterface."""

    interface_class: type
    service_name: str
    servicer_factory: Callable[["DriverRegistry"], Any]
    add_to_server: Callable[[Any, grpc.aio.Server], None]


_servicer_adapters: dict[type, ServicerAdapterInfo] = {}


def register_servicer_adapter(
    interface_class: type,
    service_name: str,
    servicer_factory: Callable[["DriverRegistry"], Any],
    add_to_server: Callable[[Any, grpc.aio.Server], None],
) -> None:
    """Register a generated servicer adapter for a DriverInterface.

    Called by generated servicer modules at import time to declare
    that a native gRPC servicer exists for a given interface.

    Args:
        interface_class: The DriverInterface subclass (e.g., PowerInterface).
        service_name: Fully-qualified gRPC service name.
        servicer_factory: Callable that takes a DriverRegistry and returns
            a servicer instance.
        add_to_server: The ``add_XServicer_to_server`` function from the
            generated ``_pb2_grpc`` module.
    """
    _servicer_adapters[interface_class] = ServicerAdapterInfo(
        interface_class=interface_class,
        service_name=service_name,
        servicer_factory=servicer_factory,
        add_to_server=add_to_server,
    )
    logger.debug("Registered servicer adapter for %s -> %s", interface_class.__name__, service_name)


def get_servicer_adapter(interface_class: type) -> ServicerAdapterInfo | None:
    """Look up the servicer adapter for a DriverInterface class."""
    return _servicer_adapters.get(interface_class)


class DriverRegistry:
    """Routes native gRPC calls to the correct driver instance.

    Each driver is registered with its UUID and the fully-qualified gRPC
    service name it implements. The ``resolve`` method reads
    ``x-jumpstarter-driver-uuid`` from gRPC call metadata to select the
    target driver.

    When only one driver is registered for a service, the UUID metadata
    may be omitted — the single instance is returned by default. When
    multiple drivers implement the same service and no UUID is provided,
    the call is aborted with ``FAILED_PRECONDITION``.
    """

    def __init__(self):
        self._by_uuid: dict[str, tuple[str, Any]] = {}  # uuid -> (service, driver)
        self._by_service: dict[str, dict[str, Any]] = {}  # service -> {uuid: driver}

    def register(self, uuid: str, service_name: str, driver: Any) -> None:
        """Register a driver instance for a native gRPC service.

        Args:
            uuid: The driver instance UUID.
            service_name: Fully-qualified gRPC service name
                (e.g., ``jumpstarter.interfaces.power.v1.PowerInterface``).
            driver: The driver instance.
        """
        self._by_uuid[uuid] = (service_name, driver)
        self._by_service.setdefault(service_name, {})[uuid] = driver
        logger.debug("Registered driver %s for service %s", uuid, service_name)

    async def resolve(self, context: grpc.aio.ServicerContext, service_name: str) -> Any:
        """Resolve the target driver from gRPC call metadata.

        Reads ``x-jumpstarter-driver-uuid`` from the invocation metadata.
        If present, looks up that specific driver. If absent and only one
        driver is registered for the service, returns it. If absent and
        multiple drivers exist, aborts with ``FAILED_PRECONDITION``.

        Args:
            context: The gRPC servicer context (used to read metadata
                and abort on error).
            service_name: Fully-qualified gRPC service name to resolve.

        Returns:
            The resolved driver instance.

        Raises:
            Aborts the RPC via ``context.abort()`` on resolution failure.
        """
        metadata = dict(context.invocation_metadata())
        uuid = metadata.get(_UUID_METADATA_KEY)

        drivers = self._by_service.get(service_name, {})
        if not drivers:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"no driver registered for {service_name}",
            )

        if uuid:
            driver = drivers.get(uuid)
            if driver is None:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"driver {uuid} not found for {service_name}",
                )
            return driver

        if len(drivers) == 1:
            return next(iter(drivers.values()))

        uuids = list(drivers.keys())
        await context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"multiple drivers for {service_name}, "
            f"specify x-jumpstarter-driver-uuid: {uuids}",
        )

    @property
    def services(self) -> dict[str, dict[str, Any]]:
        """Return the service -> {uuid: driver} mapping (read-only view)."""
        return dict(self._by_service)

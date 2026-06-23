import os
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field

from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.env import (
    JMP_DRIVERS_ALLOW,
    JMP_EXPORTER,
    JMP_EXPORTER_LABELS,
    JMP_LEASE,
    JUMPSTARTER_HOST,
)


@dataclass(frozen=True)
class ExporterMetadata:
    """Metadata about the exporter connected to the current shell session, read from the env vars
    ``jmp shell`` sets for a remote lease (``JMP_EXPORTER``/``JMP_EXPORTER_LABELS``/``JMP_LEASE``)."""

    name: str
    labels: dict[str, str] = field(default_factory=dict)
    lease: str | None = None

    @classmethod
    def from_env(cls) -> "ExporterMetadata":
        """Build metadata from JMP_EXPORTER, JMP_EXPORTER_LABELS, and JMP_LEASE env vars."""
        name = os.environ.get(JMP_EXPORTER, "")
        lease = os.environ.get(JMP_LEASE) or None

        labels: dict[str, str] = {}
        raw_labels = os.environ.get(JMP_EXPORTER_LABELS, "")
        if raw_labels:
            for pair in raw_labels.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k:
                        labels[k] = v

        return cls(name=name, labels=labels, lease=lease)


def _drivers_from_env() -> tuple[list[str], bool]:
    """The allowed driver-client packages from ``JMP_DRIVERS_ALLOW`` (comma-separated). The
    sentinel ``UNSAFE`` enables loading any package. Plain stdlib — the parent shell sets these
    env vars; no pydantic config model."""
    allow_str = os.environ.get(JMP_DRIVERS_ALLOW, "")
    allow = allow_str.split(",") if allow_str else []
    unsafe = "UNSAFE" in allow
    return allow, unsafe


@asynccontextmanager
async def env_async(portal, stack):
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    Async version of env()

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    host = os.environ.get(JUMPSTARTER_HOST, None)
    if host is None:
        raise EnvironmentVariableNotSetError(f"{JUMPSTARTER_HOST} not set")

    allow, unsafe = _drivers_from_env()

    async with client_from_path(
        host,
        portal,
        stack,
        allow=allow,
        unsafe=unsafe,
    ) as client:
        try:
            yield client
        finally:
            if hasattr(client, "close"):
                client.close()


@asynccontextmanager
async def env_with_metadata_async(portal, stack):
    """Async version of :func:`env_with_metadata`. Yields ``(client, ExporterMetadata)``; the
    metadata is read from the env vars ``jmp shell`` set (JMP_EXPORTER/JMP_EXPORTER_LABELS/JMP_LEASE).
    """
    async with env_async(portal, stack) as client:
        yield client, ExporterMetadata.from_env()


@contextmanager
def env():
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(env_async(portal, stack)) as client:
                yield client


@contextmanager
def env_with_metadata():
    """Provide a client and exporter metadata for an existing Jumpstarter shell.

    Useful when you need both the client and information about the connected exporter
    (name, labels, lease ID). Example::

        with env_with_metadata() as (client, metadata):
            print(metadata.name, metadata.labels, metadata.lease)
    """
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(env_with_metadata_async(portal, stack)) as result:
                yield result

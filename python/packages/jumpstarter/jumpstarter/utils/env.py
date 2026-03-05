import os
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field

from anyio.from_thread import start_blocking_portal

from jumpstarter.client import client_from_path
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.client import ClientConfigV1Alpha1Drivers
from jumpstarter.config.env import JMP_EXPORTER, JMP_EXPORTER_LABELS, JMP_LEASE, JUMPSTARTER_HOST


@dataclass(frozen=True)
class ExporterMetadata:
    """Metadata about the exporter connected to the current shell session."""

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
                    labels[k] = v

        return cls(name=name, labels=labels, lease=lease)


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

    drivers = ClientConfigV1Alpha1Drivers()

    async with client_from_path(
        host,
        portal,
        stack,
        allow=drivers.allow,
        unsafe=drivers.unsafe,
    ) as client:
        try:
            yield client
        finally:
            if hasattr(client, "close"):
                client.close()


@asynccontextmanager
async def env_with_metadata_async(portal, stack):
    """Provide a client and exporter metadata for an existing Jumpstarter shell.

    Async version of env_with_metadata()

    Yields a (client, ExporterMetadata) tuple. The metadata is read from environment
    variables set by ``jmp shell``: JMP_EXPORTER, JMP_EXPORTER_LABELS, and JMP_LEASE.
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

    This is useful when you need both the client and information about the connected
    exporter (name, labels, lease ID).

    Example::

        with env_with_metadata() as (client, metadata):
            print(metadata.name)
            print(metadata.labels)
            print(metadata.lease)
    """
    with start_blocking_portal() as portal:
        with ExitStack() as stack:
            with portal.wrap_async_context_manager(env_with_metadata_async(portal, stack)) as result:
                yield result

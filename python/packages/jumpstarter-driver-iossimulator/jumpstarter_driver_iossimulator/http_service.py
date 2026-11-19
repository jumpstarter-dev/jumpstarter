"""HTTPS providers selected by trusted exporter configuration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.importlib import import_class


@dataclass(frozen=True)
class SimulatorHttpContext:
    """Immutable identity and filesystem ownership passed to a provider."""

    directory: Path
    device_set: Path
    udid: str
    platform_version: str

    def device_metadata(self):
        return {"udid": self.udid, "platform": "iOS", "version": self.platform_version}


class HttpServiceProvider(ABC):
    """An HTTPS service scoped to one simulator lease.

    Constructors must remove partial resources before raising. ``close`` must
    be idempotent and revoke existing streams as well as prevent new ones. A
    close failure leaves the provider and device set owned for a cleanup retry.
    Providers must not discover, stop or mutate resources outside their context.
    """

    @abstractmethod
    def metadata(self) -> dict:
        """Return public ca_certificate PEM and a JSON metadata object."""

    @abstractmethod
    def connect(self):
        """Return an async context manager yielding the encrypted byte stream."""

    @abstractmethod
    def close(self) -> None:
        """Revoke streams, stop owned processes and remove private state."""


def validate_http_service(config):
    if config is None:
        return
    if not isinstance(config, dict) or set(config) - {"provider", "options"}:
        raise ConfigurationError("http_service requires a provider class path and optional options object")
    provider = config.get("provider")
    options = config.get("options", {})
    if not isinstance(provider, str) or not provider.strip() or "\x00" in provider or "." not in provider:
        raise ConfigurationError("http_service.provider must be a nonempty dotted class path")
    if not isinstance(options, dict) or not all(isinstance(key, str) for key in options) or "context" in options:
        raise ConfigurationError("http_service.options must be an object; context is reserved")


def create_http_service(config, context):
    validate_http_service(config)
    # The import and options are exporter configuration, never RPC arguments.
    provider = import_class(config["provider"], allow=[], unsafe=True)
    if not isinstance(provider, type) or not issubclass(provider, HttpServiceProvider):
        raise ConfigurationError("http_service.provider must implement HttpServiceProvider")
    return provider(context=context, **config.get("options", {}))

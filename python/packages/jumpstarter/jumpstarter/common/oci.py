"""OCI registry authentication utilities.

This module provides functions for reading container registry credentials
from standard auth files (Docker config.json / Podman auth.json) as a
fallback when explicit credentials (e.g. OCI_USERNAME/OCI_PASSWORD env vars)
are not provided.

Supported auth file locations (checked in order):
1. $REGISTRY_AUTH_FILE (explicit override)
2. ${XDG_RUNTIME_DIR}/containers/auth.json (Podman default)
3. ~/.config/containers/auth.json (Podman fallback)
4. $DOCKER_CONFIG/config.json or ~/.docker/config.json (Docker)
"""

import base64
import binascii
import json
import logging
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

__all__ = [
    "OciCredentials",
    "parse_oci_registry",
    "read_auth_file_credentials",
    "resolve_oci_credentials",
]


class OciCredentials(BaseModel):
    """Resolved OCI registry credentials.

    Construction enforces that username and password are both set or both None.
    Passing only one raises ``ValidationError``.
    """

    model_config = ConfigDict(frozen=True)

    username: str | None = None
    password: SecretStr | None = Field(default=None)

    @field_validator("username", "password", mode="before")
    @classmethod
    def _normalize_empty(cls, v: str | SecretStr | None) -> str | None:
        if isinstance(v, SecretStr):
            v = v.get_secret_value()
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v

    @property
    def plain_password(self) -> str | None:
        """Return the raw password string, or None if unset."""
        return self.password.get_secret_value() if self.password else None

    @property
    def is_authenticated(self) -> bool:
        return self.username is not None and self.password is not None

    @model_validator(mode="after")
    def _check_both_or_neither(self) -> "OciCredentials":
        if bool(self.username) != bool(self.password):
            raise ValueError("OCI authentication requires both username and password")
        return self


def _get_registries_conf_paths() -> list[Path]:
    """Return ordered list of registries.conf paths to search."""
    return [
        Path.home() / ".config" / "containers" / "registries.conf",
        Path("/etc/containers/registries.conf"),
        Path("/usr/share/containers/registries.conf"),
    ]


@lru_cache(maxsize=1)
def _get_unqualified_search_registries() -> tuple[str, ...]:
    """Read unqualified-search-registries from containers registries.conf.

    Falls back to ``("docker.io",)`` if no config is found.
    """
    for conf_path in _get_registries_conf_paths():
        if not conf_path.is_file():
            continue
        try:
            with open(conf_path, "rb") as f:
                data = tomllib.load(f)
            registries = tuple(
                r for r in data.get("unqualified-search-registries", []) if isinstance(r, str) and r.strip()
            )
            if registries:
                logger.debug("Read unqualified-search-registries from %s: %s", conf_path, registries)
                return registries
        except (OSError, tomllib.TOMLDecodeError) as e:
            logger.debug("Skipping registries.conf %s: %s", conf_path, e)
            continue
    return ("docker.io",)


def _parse_registries_for_url(oci_url: str) -> tuple[str, ...]:
    """Return possible registries for an OCI URL.

    For explicit registry URLs, returns a single-element tuple.
    For bare image names (e.g. ``ubuntu:latest``), returns the host's
    configured ``unqualified-search-registries``.
    """
    url = oci_url
    if url.startswith("oci://"):
        url = url[len("oci://") :]

    # Strip digest references before parsing — "ubuntu@sha256:abc" would
    # otherwise have the colon corrupt port/tag disambiguation.
    url = re.sub(r"@sha(256|384|512):[a-fA-F0-9]+", "", url)

    parts = url.split("/", 1)
    registry = parts[0]

    if "/" not in url:
        if ":" in registry:
            host_port = registry.split(":", 1)
            if host_port[1].isdigit():
                return (registry,)  # registry:port
            return _get_unqualified_search_registries()  # bare image like "ubuntu:latest"
        if "." not in registry and registry != "localhost":
            return _get_unqualified_search_registries()  # bare image like "ubuntu"
    else:
        # namespace/image form (e.g. "library/ubuntu") — first segment has
        # no dot and isn't localhost, so it's not a registry hostname.
        if "." not in registry and registry != "localhost":
            if ":" not in registry or not registry.split(":", 1)[1].isdigit():
                return _get_unqualified_search_registries()

    return (registry,)


def parse_oci_registry(oci_url: str) -> str:
    """Extract the registry hostname from an OCI URL.

    Handles URLs in the format ``oci://registry/repo:tag`` as well as plain
    image references like ``registry/repo:tag``. For bare image names,
    returns the first entry from the host's ``unqualified-search-registries``
    (defaults to ``docker.io``).

    Args:
        oci_url: OCI image reference, optionally prefixed with ``oci://``.

    Returns:
        Registry hostname (with port if present), e.g. ``quay.io`` or
        ``registry.example.com:5000``.
    """
    return _parse_registries_for_url(oci_url)[0]


def _get_auth_file_paths() -> list[Path]:
    """Return the ordered list of auth file paths to search.

    Returns:
        List of Path objects pointing to potential auth files, in priority order.
    """
    paths: list[Path] = []

    # 1. Explicit override via $REGISTRY_AUTH_FILE
    registry_auth_file = os.environ.get("REGISTRY_AUTH_FILE")
    if registry_auth_file:
        paths.append(Path(registry_auth_file))

    # 2. Podman default: ${XDG_RUNTIME_DIR}/containers/auth.json
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        paths.append(Path(xdg_runtime) / "containers" / "auth.json")

    # 3. Podman fallback: ~/.config/containers/auth.json
    paths.append(Path.home() / ".config" / "containers" / "auth.json")

    # 4. Docker: $DOCKER_CONFIG/config.json or ~/.docker/config.json
    docker_config = os.environ.get("DOCKER_CONFIG")
    if docker_config:
        paths.append(Path(docker_config) / "config.json")
    paths.append(Path.home() / ".docker" / "config.json")

    return paths


def _normalize_registry(registry: str) -> str:
    """Normalize a registry hostname for matching.

    Handles variations like ``https://index.docker.io/v1/`` vs ``docker.io``.

    Args:
        registry: Registry hostname or URL to normalize.

    Returns:
        Normalized registry string for comparison.
    """
    # Strip URL scheme and trailing slashes/paths
    if "://" in registry:
        parsed = urlparse(registry)
        registry = parsed.netloc or parsed.path.split("/")[0]

    registry = registry.rstrip("/")

    # Normalize Docker Hub references
    docker_hub_aliases = {
        "index.docker.io",
        "registry-1.docker.io",
        "registry.hub.docker.com",
    }
    # Strip port if default
    host = registry.split(":")[0]
    if host in docker_hub_aliases or host == "docker.io":
        return "docker.io"

    return registry


def _lookup_credentials_in_auth_data(auth_data: dict[str, Any], registry: str) -> OciCredentials:
    """Look up credentials for a registry in parsed auth file data.

    Args:
        auth_data: Parsed JSON content of an auth file.
        registry: Normalized registry hostname to look up.

    Returns:
        OciCredentials with both fields set, or ``OciCredentials()`` if not found.
    """
    auths = auth_data.get("auths", {})
    if not auths:
        return OciCredentials()

    # Try to find a matching entry — normalize all keys for comparison
    for key, value in auths.items():
        if not isinstance(value, dict):
            continue
        if _normalize_registry(key) == registry:
            # The "auth" field is base64(username:password)
            auth_b64 = value.get("auth")
            if auth_b64:
                try:
                    decoded = base64.b64decode(auth_b64, validate=True).decode("utf-8")
                    username, password = decoded.split(":", 1)
                    if username and password:
                        return OciCredentials(username=username, password=password)
                except (binascii.Error, ValueError, UnicodeDecodeError) as e:
                    logger.warning("Failed to decode auth entry for %s: %s", key, e)

            # Some auth files use separate username/password fields
            username = value.get("username")
            password = value.get("password")
            if username and password:
                try:
                    return OciCredentials(username=username, password=password)
                except (ValueError, ValidationError) as e:
                    logger.warning("Failed to validate auth entry for %s: %s", key, e)

    return OciCredentials()


def read_auth_file_credentials(oci_url: str) -> OciCredentials:
    """Read registry credentials from container auth files.

    Searches standard auth file locations for credentials matching the
    registry in the given OCI URL. For bare image names, tries all
    registries from ``unqualified-search-registries`` in registries.conf.
    Returns the first match found.

    Args:
        oci_url: OCI image reference (e.g. ``oci://quay.io/org/image:tag``).

    Returns:
        OciCredentials with both fields set, or ``OciCredentials()`` with
        both fields None if no credentials are found.
    """
    registries = _parse_registries_for_url(oci_url)

    for registry in registries:
        normalized_registry = _normalize_registry(registry)

        for auth_path in _get_auth_file_paths():
            if not auth_path.is_file():
                continue

            try:
                auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Skipping auth file %s: %s", auth_path, e)
                continue

            creds = _lookup_credentials_in_auth_data(auth_data, normalized_registry)
            if creds.is_authenticated:
                logger.info("Found OCI registry credentials for %s in %s", registry, auth_path)
                return creds

    logger.debug("No credentials found for %s in any auth file", registries)
    return OciCredentials()


def resolve_oci_credentials(
    oci_url: str,
    username: str | None = None,
    password: str | None = None,
) -> OciCredentials:
    """Resolve OCI registry credentials with three-level precedence.

    1. Explicit ``username``/``password`` arguments (if either is non-empty).
    2. ``OCI_USERNAME``/``OCI_PASSWORD`` environment variables.
    3. Container auth files (auth.json / Docker config.json).

    Args:
        oci_url: OCI image reference (e.g. ``oci://quay.io/org/image:tag``).
        username: Explicit registry username (takes highest priority).
        password: Explicit registry password (takes highest priority).

    Returns:
        OciCredentials with both fields set, or both None.

    Raises:
        ValueError: If only one of username/password is provided
            (at explicit or env-var level).
    """
    # Level 1: Explicit arguments
    if username is not None or password is not None:
        try:
            creds = OciCredentials(username=username, password=password)
        except ValidationError:
            raise ValueError("OCI authentication requires both username and password") from None
        if creds.is_authenticated:
            return creds

    # Level 2: Environment variables
    env_username = os.environ.get("OCI_USERNAME")
    env_password = os.environ.get("OCI_PASSWORD")

    if env_username is not None or env_password is not None:
        try:
            creds = OciCredentials(username=env_username, password=env_password)
        except ValidationError:
            logger.warning(
                "Only one of OCI_USERNAME/OCI_PASSWORD is set; "
                "ignoring partial env credentials and checking auth files"
            )
        else:
            if creds.is_authenticated:
                logger.info("Using OCI registry credentials from environment variables")
                return creds

    # Level 3: Auth files
    return read_auth_file_credentials(oci_url)

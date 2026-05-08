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
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def parse_oci_registry(oci_url: str) -> str:
    """Extract the registry hostname from an OCI URL.

    Handles URLs in the format ``oci://registry/repo:tag`` as well as plain
    image references like ``registry/repo:tag``.

    Args:
        oci_url: OCI image reference, optionally prefixed with ``oci://``.

    Returns:
        Registry hostname (with port if present), e.g. ``quay.io`` or
        ``registry.example.com:5000``.
    """
    url = oci_url
    if url.startswith("oci://"):
        url = url[len("oci://") :]

    # Strip any tag or digest suffix for parsing purposes
    # e.g. "quay.io/org/repo:tag" -> we just need "quay.io"
    # The registry is the first path component
    parts = url.split("/", 1)
    registry = parts[0]

    # Remove tag/digest if someone passed just "registry:tag" with no path
    if "/" not in url and ":" in registry:
        # Could be registry:port or image:tag — if the part after : is numeric
        # it's a port, otherwise it's a tag on a Docker Hub image
        host_port = registry.split(":", 1)
        if host_port[1].isdigit():
            return registry  # registry:port
        return "docker.io"  # bare image like "ubuntu:latest"

    return registry


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


def _lookup_credentials_in_auth_data(auth_data: dict, registry: str) -> tuple[str | None, str | None]:
    """Look up credentials for a registry in parsed auth file data.

    Args:
        auth_data: Parsed JSON content of an auth file.
        registry: Normalized registry hostname to look up.

    Returns:
        Tuple of (username, password), or (None, None) if not found.
    """
    auths = auth_data.get("auths", {})
    if not auths:
        return None, None

    # Try to find a matching entry — normalize all keys for comparison
    for key, value in auths.items():
        if _normalize_registry(key) == registry:
            # The "auth" field is base64(username:password)
            auth_b64 = value.get("auth")
            if auth_b64:
                try:
                    decoded = base64.b64decode(auth_b64).decode("utf-8")
                    username, password = decoded.split(":", 1)
                    return username, password
                except (ValueError, UnicodeDecodeError) as e:
                    logger.warning(f"Failed to decode auth entry for {key}: {e}")
                    continue

            # Some auth files use separate username/password fields
            username = value.get("username")
            password = value.get("password")
            if username and password:
                return username, password

    return None, None


def read_auth_file_credentials(
    oci_url: str,
) -> tuple[str | None, str | None]:
    """Read registry credentials from container auth files.

    Searches standard auth file locations for credentials matching the
    registry in the given OCI URL. Returns the first match found.

    Args:
        oci_url: OCI image reference (e.g. ``oci://quay.io/org/image:tag``).

    Returns:
        Tuple of (username, password), or (None, None) if no credentials
        are found.
    """
    registry = parse_oci_registry(oci_url)
    normalized_registry = _normalize_registry(registry)

    for auth_path in _get_auth_file_paths():
        if not auth_path.is_file():
            continue

        try:
            auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Skipping auth file {auth_path}: {e}")
            continue

        username, password = _lookup_credentials_in_auth_data(auth_data, normalized_registry)
        if username and password:
            logger.info(f"Found OCI registry credentials for {registry} in {auth_path}")
            return username, password

    logger.debug(f"No credentials found for registry {registry} in any auth file")
    return None, None


def _read_password_file() -> str | None:
    """Read OCI password from OCI_PASSWORD_FILE if set.

    Supports projected service-account tokens on OpenShift / Kubernetes
    where the kubelet rotates the token file periodically.
    """
    password_file = os.environ.get("OCI_PASSWORD_FILE")
    if not password_file:
        return None
    try:
        with open(password_file) as f:
            password = f.read().strip()
        if password:
            logger.info("Read OCI password from OCI_PASSWORD_FILE")
            return password
    except OSError as e:
        logger.warning(f"Failed to read OCI_PASSWORD_FILE ({password_file}): {e}")
    return None


def resolve_oci_credentials(oci_url: str) -> tuple[str | None, str | None]:
    """Resolve OCI registry credentials from environment or auth files.

    Resolution order:
    1. OCI_USERNAME + OCI_PASSWORD environment variables
    2. OCI_USERNAME + OCI_PASSWORD_FILE (for projected SA tokens)
    3. Container auth files (auth.json / Docker config.json)

    Args:
        oci_url: OCI image reference (e.g. ``oci://quay.io/org/image:tag``).

    Returns:
        Tuple of (username, password), or (None, None) if no credentials
        are found from any source.
    """
    username = os.environ.get("OCI_USERNAME")
    password = os.environ.get("OCI_PASSWORD")

    if not password:
        password = _read_password_file()

    if username and password:
        logger.info("Using OCI registry credentials from environment variables")
        return username, password

    if username or password:
        logger.warning(
            "Only one of OCI_USERNAME/OCI_PASSWORD is set; ignoring partial env credentials and checking auth files"
        )

    return read_auth_file_credentials(oci_url)

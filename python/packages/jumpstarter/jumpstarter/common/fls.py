"""FLS (Flasher) binary utilities.

This module provides functions for locating the FLS binary tool
used for flashing devices via fastboot with OCI image support.

FLS is pre-installed in the container image for security and reliability,
with optional configuration-based overrides for testing and flexibility.
"""

import logging
import os
import platform
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

FLS_GITHUB_REPO = "jumpstarter-dev/fls"


def get_fls_github_url(version: str, arch: str | None = None) -> str:
    """Get GitHub release URL for FLS version.

    Args:
        version: FLS version (e.g., "0.1.9")
        arch: Target architecture (e.g., "aarch64", "x86_64"). If None,
              auto-detects from current platform.

    Returns:
        Download URL for the architecture-appropriate binary
    """
    if arch is None:
        arch = platform.machine().lower()
    else:
        arch = arch.lower()
    if arch in ("aarch64", "arm64"):
        binary_name = "fls-aarch64-linux"
    elif arch in ("x86_64", "amd64"):
        binary_name = "fls-x86_64-linux"
    else:
        binary_name = "fls-aarch64-linux"  # Default to aarch64

    return f"https://github.com/{FLS_GITHUB_REPO}/releases/download/{version}/{binary_name}"


def download_fls(url: str, timeout: float = 30.0) -> str:
    """Download FLS binary from URL to a temp file with atomic operations.

    Args:
        url: URL to download FLS binary from
        timeout: Download timeout in seconds

    Returns:
        Path to the downloaded binary

    Raises:
        RuntimeError: If download fails
    """
    fd, binary_path = tempfile.mkstemp(prefix="fls-")
    os.close(fd)
    temp_path = f"{binary_path}.part"

    try:
        logger.info(f"Downloading FLS binary from: {url}")
        with urllib.request.urlopen(url, timeout=timeout) as response:
            with open(temp_path, 'wb') as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())

        # Set permissions on temp file before rename
        Path(temp_path).chmod(0o755)

        # Atomic rename to final location
        os.replace(temp_path, binary_path)

        logger.info(f"FLS binary downloaded to: {binary_path}")
        return binary_path

    except Exception as e:
        # Clean up temp files
        Path(temp_path).unlink(missing_ok=True)
        Path(binary_path).unlink(missing_ok=True)
        logger.error(f"Failed to download FLS from {url}: {e}")
        raise RuntimeError(f"Failed to download FLS from {url}: {e}") from e


def get_fls_binary(
    fls_version: str | None = None,
    fls_binary_url: str | None = None,
    allow_custom_binaries: bool = False,
) -> str:
    """Get path to FLS binary with configuration-based overrides.

    Args:
        fls_version: Optional FLS version to download from GitHub releases
        fls_binary_url: Custom URL to download FLS binary from
        allow_custom_binaries: Whether custom binary URLs are allowed

    Returns:
        Path to the FLS binary

    Raises:
        RuntimeError: If custom binary URL provided but not allowed

    Note:
        Priority order:
        1. fls_binary_url (if allow_custom_binaries=True)
        2. fls_version from GitHub releases
        3. Pre-installed system binary
    """
    if fls_binary_url:
        if not allow_custom_binaries:
            raise RuntimeError(
                "Custom FLS binary URLs are disabled for security. "
                "Set allow_custom_binaries=True in driver configuration to enable."
            )
        logger.warning(
            f"⚠️  SECURITY: Downloading custom FLS binary from {fls_binary_url}. "
            "Ensure this URL is trusted and secure."
        )
        return download_fls(fls_binary_url)

    if fls_version:
        github_url = get_fls_github_url(fls_version)
        logger.warning(f"Downloading FLS version {fls_version} from GitHub: {github_url}")
        return download_fls(github_url)

    logger.debug("Using pre-installed FLS from system PATH")
    return "fls"

"""The pinned ``scrcpy-server``: where it comes from, and how it is verified before it goes onto a device.

``scrcpy-server`` is the Java program scrcpy pushes to an Android device and runs as the ``shell`` user. It is **not**
part of this package (it is a binary of the scrcpy project, Apache-2.0: https://github.com/Genymobile/scrcpy). The
driver pins one release by version and SHA-256, finds it on the exporter, checks the hash, and only then pushes it; a
file whose hash differs is refused, whoever put it there.

Where to get it (once, when the exporter image is built; nothing is ever downloaded at runtime)::

    python -m jumpstarter_driver_adb.hmi.fetch --dest /opt/scrcpy

fetches ``scrcpy-server-v4.1`` from the GitHub release (or from a mirror, with ``--base-url``) and verifies it against
the pinned hash below. The same file ships in the scrcpy packages (``/opt/homebrew/share/scrcpy/scrcpy-server`` on
Homebrew, ``/usr/share/scrcpy/scrcpy-server`` on Debian and Ubuntu). The release also publishes ``SHA256SUMS.txt``
with a PGP signature (``doc/verify-release.md`` in scrcpy's source), which is how the pin below was checked against
the project's own statement of the hash.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .protocol import SERVER_VERSION
from jumpstarter.common.exceptions import ConfigurationError

#: The release the driver speaks (the server and the client must be the exact same version).
PINNED_VERSION = SERVER_VERSION
#: SHA-256 of ``scrcpy-server-v4.1`` (the GitHub release asset, 733706 bytes), as published in the release's own
#: metadata.
PINNED_SHA256 = "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
#: Where the releases are published; a mirror with the same layout (``<base>/v<version>/scrcpy-server-v<version>``) can
#: stand in for it (see :mod:`jumpstarter_driver_adb.hmi.fetch`).
RELEASE_BASE_URL = "https://github.com/Genymobile/scrcpy/releases/download"
#: The asset of the pinned release under :data:`RELEASE_BASE_URL`.
RELEASE_ASSET = f"v{PINNED_VERSION}/scrcpy-server-v{PINNED_VERSION}"
#: The pinned release's asset on GitHub.
RELEASE_URL = f"{RELEASE_BASE_URL}/{RELEASE_ASSET}"
#: ``$SCRCPY_SERVER`` names the file to use when the configuration does not.
ENV_SERVER = "SCRCPY_SERVER"

#: Where a scrcpy installation keeps the server, searched in this order when nothing is configured.
SEARCH_PATHS = (
    "/opt/scrcpy/scrcpy-server",
    "/usr/local/share/scrcpy/scrcpy-server",
    "/usr/share/scrcpy/scrcpy-server",
    "/opt/homebrew/share/scrcpy/scrcpy-server",
)


def sha256_of(path: Path) -> str:
    """The SHA-256 of a file, as lower-case hex."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate(configured: str | None = None) -> Path:
    """The server file to use: the configured path, else ``$SCRCPY_SERVER``, else the first of :data:`SEARCH_PATHS` that
    exists.

    Raises:
        ConfigurationError: If there is none (the message says how to get one).
    """
    candidates = [configured] if configured else [os.environ.get(ENV_SERVER) or None, *SEARCH_PATHS]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    named = f" {configured!r}" if configured else ""
    raise ConfigurationError(
        f"scrcpy-server{named} not found. Put scrcpy-server v{PINNED_VERSION} on the exporter "
        f"(`python -m jumpstarter_driver_adb.hmi.fetch --dest DIR`, or install scrcpy) and set `server_path` "
        f"(or ${ENV_SERVER}). Searched: {', '.join(c for c in candidates if c) or '-'}"
    )


def _refusal(path: Path, actual: str, expected_sha256: str) -> ConfigurationError:
    return ConfigurationError(
        f"{path} is not the pinned scrcpy-server v{PINNED_VERSION}: sha256 {actual}, expected {expected_sha256}. "
        "Refusing to push it to a device. Fetch the pinned file "
        "(`python -m jumpstarter_driver_adb.hmi.fetch --dest DIR`) or, to use another release deliberately, set "
        f"`server_sha256` (and note that the protocol module speaks only v{PINNED_VERSION})."
    )


def verify(path: Path, expected_sha256: str = PINNED_SHA256) -> str:
    """Check ``path`` against the pinned hash and return the hash.

    Raises:
        ConfigurationError: If the hash differs (the file is another build, or has been changed).
    """
    actual = sha256_of(path)
    if actual != expected_sha256.lower():
        raise _refusal(path, actual, expected_sha256)
    return actual


def read_verified(path: Path, expected_sha256: str = PINNED_SHA256) -> bytes:
    """The file's bytes, once, checked against the pinned hash: what gets pushed is exactly what was verified (not the
    path, which could be written to between the check and the push).

    Raises:
        ConfigurationError: If the hash differs.
    """
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise _refusal(path, actual, expected_sha256)
    return data


def device_path(sha256: str, nonce: str = "") -> str:
    """Where the server goes on the device: the one directory ``shell`` may write, a name of this build and (with
    ``nonce``) of one session."""
    suffix = f"-{nonce}" if nonce else ""
    return f"/data/local/tmp/jmp-scrcpy-server-{PINNED_VERSION}-{sha256[:12]}{suffix}.jar"

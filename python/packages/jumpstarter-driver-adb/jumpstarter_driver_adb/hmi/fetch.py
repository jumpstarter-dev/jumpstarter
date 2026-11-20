"""``python -m jumpstarter_driver_adb.hmi.fetch --dest DIR``: download the pinned ``scrcpy-server`` and verify it.

Run it when an exporter image is built, not at runtime: the driver itself never touches the network. The downloaded
bytes go to a temporary file in ``DIR``, are checked against
:data:`~jumpstarter_driver_adb.hmi.server_binary.PINNED_SHA256`, and only then renamed to ``DIR/scrcpy-server``; a
download that does not match is deleted and the command fails.

The default source is the project's GitHub release. ``--base-url`` points the download at a mirror with the same layout
(``<base-url>/v4.1/scrcpy-server-v4.1``); the SHA-256 check is what makes a mirror safe to use.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .server_binary import PINNED_SHA256, PINNED_VERSION, RELEASE_ASSET, RELEASE_BASE_URL, verify
from jumpstarter.common.exceptions import ConfigurationError

#: How long a download may stall (seconds), not how long it may take.
TIMEOUT_S = 60


def release_url(base_url: str = RELEASE_BASE_URL) -> str:
    """The pinned asset's URL under ``base_url`` (a mirror laid out like the GitHub releases).

    Raises:
        ConfigurationError: If ``base_url`` is not an ``http`` or ``https`` URL.
    """
    if urlsplit(base_url).scheme not in ("http", "https"):
        raise ConfigurationError(f"the base URL must be an http or https URL: {base_url!r}")
    return f"{base_url.rstrip('/')}/{RELEASE_ASSET}"


def fetch(dest: Path, base_url: str = RELEASE_BASE_URL, expected_sha256: str | None = None) -> Path:
    """Download the pinned asset from ``base_url`` into ``dest/scrcpy-server`` if it matches ``expected_sha256``
    (default: the pinned hash); the path of the file.

    Raises:
        ConfigurationError: If the URL is not http(s) or the download is not the pinned build (nothing is left behind).
        OSError: If the download fails.
    """
    url = release_url(base_url)
    dest.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".scrcpy-server.", dir=dest)
    try:
        with os.fdopen(handle, "wb") as out, urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        verify(Path(temporary), expected_sha256 or PINNED_SHA256)
        target = dest / "scrcpy-server"
        os.replace(temporary, target)
        target.chmod(0o644)
        return target
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """The command line."""
    parser = argparse.ArgumentParser(description=f"Fetch and verify scrcpy-server v{PINNED_VERSION}.")
    parser.add_argument("--dest", type=Path, required=True, help="directory that gets the file `scrcpy-server`")
    parser.add_argument(
        "--base-url",
        default=RELEASE_BASE_URL,
        help="where the releases are published; a mirror may stand in (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        target = fetch(args.dest, args.base_url)
    except ConfigurationError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"could not download from {args.base_url}: {e}", file=sys.stderr)
        return 1
    print(f"{target}  sha256 {PINNED_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

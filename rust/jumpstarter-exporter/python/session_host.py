"""Driver-host subprocess for the Rust exporter core (Phase B, increment 1).

Serves the Jumpstarter ``ExporterService`` + ``RouterService`` for an exporter
config on a Unix socket (forced to ``LEASE_READY``), prints the socket path on
stdout, and serves until killed. The Rust exporter core spawns this, reads the
path, registers with the controller, and bridges the router to this socket.

Hosts the real Python ``Driver`` objects so the existing driver ecosystem works
unchanged (spec 09 §3.2, driver-host boundary option a).

Usage: ``session_host.py <exporter-config-path>``
"""

import sys
from pathlib import Path

import anyio

from jumpstarter.config.exporter import ExporterConfigV1Alpha1


async def main(config_path: str) -> None:
    config = ExporterConfigV1Alpha1.load_path(Path(config_path))
    async with config.serve_unix_async() as socket_path:
        # The Rust core reads this line to learn where to bridge.
        print(socket_path, flush=True)
        await anyio.sleep_forever()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: session_host.py <exporter-config-path>", file=sys.stderr)
        sys.exit(2)
    anyio.run(main, sys.argv[1])

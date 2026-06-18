"""Slim per-lease driver host for the Rust exporter core (native migration).

Instantiates the WHOLE driver tree from an exporter config and serves the
driver-level ``ExporterService`` + ``RouterService`` on a **single** private Unix
socket for the Rust core to proxy into. Unlike ``session_host.py`` it serves no hook
socket and owns no lease lifecycle — the Rust core owns the client/hook-facing
protocol, status, EndSession, and the lease FSM. This process is reduced to the
Python ``Driver`` dispatch engine (marker lookup, ``google.protobuf.Value``
marshaling, the exception→status table, the resource-handle FSM, and the stream
codecs), reused verbatim.

The whole tree lives in one process on purpose: ``enumerate()``/Proxy resolution/
``reset()``/``close()`` are in-process tree operations; "per-driver-instance" is
realized as Rust routing by UUID into this one host, not one OS process per driver.

Prints exactly ONE line (the socket path) on stdout, then serves until killed.

Usage: ``slim_driver_host.py <exporter-config-path>``
"""

import os
import sys
from pathlib import Path

import anyio

from jumpstarter.common import ExporterStatus
from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1,
    ExporterConfigV1Alpha1DriverInstance,
)
from jumpstarter.exporter import Session


async def main(config_path: str) -> None:
    config = ExporterConfigV1Alpha1.load_path(Path(config_path))
    root_device = ExporterConfigV1Alpha1DriverInstance(
        type="jumpstarter_driver_composite.driver.Composite",
        description=config.description,
        children=config.export,
    ).instantiate()

    with Session(root_device=root_device) as session:
        async with session.serve_unix_async() as path:
            # Keep the session's (no-op) driver-call gate permanently permissive; the
            # Rust core owns the real lease lifecycle and status reporting.
            session.update_status(ExporterStatus.LEASE_READY)
            # Debug knob to simulate a slow spawn (heavy driver imports) so the Rust
            # core's pre-warm pipeline can be exercised; no effect unless set.
            delay = float(os.environ.get("JMP_SLIM_HOST_DELAY", "0"))
            if delay:
                await anyio.sleep(delay)
            # The Rust core reads exactly this one line to learn where to proxy.
            print(path, flush=True)
            await anyio.sleep_forever()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: slim_driver_host.py <exporter-config-path>", file=sys.stderr)
        sys.exit(2)
    anyio.run(main, sys.argv[1])

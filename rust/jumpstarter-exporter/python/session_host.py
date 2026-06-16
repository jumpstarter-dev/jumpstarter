"""Driver-host subprocess for the Rust exporter core (Phase B).

Serves the Jumpstarter ``ExporterService`` + ``RouterService`` for an exporter
config on **two** Unix sockets — a main socket for client traffic and an isolated
hook socket for hook ``j`` commands (so they can't corrupt client LogStream frames,
see ``session.py:244-257``). Both socket paths are printed on stdout (main first,
hook second); the Rust core bridges the router to the main socket and points hook
subprocesses at the hook socket via ``JUMPSTARTER_HOST``.

The session is held at ``LEASE_READY`` for its whole life: the Rust core owns the
lease lifecycle and reports lifecycle status to the *controller*, while the session's
own driver-call status gate stays permissive (it is a no-op on main anyway, see
``session.py:268-292``). Hosts the real Python ``Driver`` objects so the existing
driver ecosystem works unchanged (spec 09 §3.2, driver-host boundary option a).

Usage: ``session_host.py <exporter-config-path>``
"""

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
        async with session.serve_unix_with_hook_socket_async() as (main_path, hook_path):
            session.update_status(ExporterStatus.LEASE_READY)
            # The Rust core reads these two lines to learn where to bridge (main)
            # and where to point hooks (hook). Order matters.
            print(main_path, flush=True)
            print(hook_path, flush=True)
            await anyio.sleep_forever()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: session_host.py <exporter-config-path>", file=sys.stderr)
        sys.exit(2)
    anyio.run(main, sys.argv[1])

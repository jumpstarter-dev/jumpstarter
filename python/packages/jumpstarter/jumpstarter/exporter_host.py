"""``python -m jumpstarter.exporter_host --serve <uds>`` — serve ONE entry's driver subtree.

The polyglot exporter hub (``jumpstarter_core.run_exporter_polyglot``) spawns one of these per
top-level ``export:`` entry, passing that entry's single-entry config **on stdin** (no temp
file) and a private ``--serve <uds>``. This process only instantiates the entry's driver tree
(via ``jumpstarter.exporter.host.DriverHostFactory``) and hands it to the embedded Rust core
(``jumpstarter_core.serve_driver_host``), which serves the driver-host gRPC seam on the socket.
The hub dials the socket and federates the entries by UUID. No controller, lease, or hooks here
— the hub owns those. The Python here is reduced to driver instantiation + dispatch.
"""

import asyncio
import os
import sys
import threading
import time

import anyio
from jumpstarter_core import serve_driver_host
from jumpstarter_core.jumpstarter_core import uniffi_set_event_loop


def _exit_when_hub_dies(poll_interval: float = 0.25) -> None:
    """Exit if the hub dies before it can SIGKILL us, so a host never leaks.

    The hub SIGKILLs each host on lease teardown, but if the hub itself dies *ungracefully*
    (SIGKILL, crash, OOM) that never runs and the host would orphan to init and keep running —
    idle hosts piling up across runs. The Python host owns this natively, in Python: terminating
    a Python process from the embedded Rust core is fragile (CPython interpreter finalization
    deadlocks on exit from a foreign thread).

    We watch the **hub's own pid** (``JMP_HUB_PID``), not our own parent: a host can be reparented
    to init the instant it spawns, so a ``getppid``-change check never fires for it — but the
    hub's pid is stable. ``os.kill(pid, 0)`` is the POSIX liveness probe (one code path for macOS
    and Linux). This runs on a dedicated daemon **thread**, not the event loop: while serving, the
    host's main thread parks in the asyncio selector (``kevent``/``epoll``) with the GIL released,
    so a plain thread is reliably scheduled — whereas an asyncio task is starved because the uniffi
    ``await`` does not cycle the loop's timers. ``os._exit`` terminates immediately (no atexit /
    interpreter finalization), exactly what the hub's SIGKILL would have done.
    """
    hub_pid = os.environ.get("JMP_HUB_PID")
    if hub_pid is None:
        return  # not spawned by the hub (e.g. a direct invocation) — nothing to watch.
    hub_pid = int(hub_pid)
    while True:
        try:
            os.kill(hub_pid, 0)  # signal 0 = liveness probe; raises if the hub is gone.
        except ProcessLookupError:
            os._exit(0)
        time.sleep(poll_interval)


def _usage() -> None:
    print(
        "usage: python -m jumpstarter.exporter_host --serve <uds>  (config on stdin)",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _parse_serve(argv: list[str]) -> str:
    uds = None
    i = 0
    while i < len(argv):
        if argv[i] == "--serve" and i + 1 < len(argv):
            uds = argv[i + 1]
            i += 2
        else:
            i += 1
    if uds is None:
        _usage()
    return uds


def main(argv: list[str]) -> None:
    uds = _parse_serve(argv)
    # Tie our lifetime to the hub's, natively in Python (see _exit_when_hub_dies).
    threading.Thread(target=_exit_when_hub_dies, name="parent-death-watch", daemon=True).start()
    # The hub streams the single-entry config YAML on stdin (closed with EOF).
    config_yaml = sys.stdin.read()

    async def serve():
        from jumpstarter.exporter.host import DriverHostFactory

        # Foreign async callbacks (the Python driver methods Rust invokes) run on Rust/tokio
        # worker threads where asyncio.get_running_loop() raises; register this loop with UniFFI
        # so they schedule onto it (mirrors jumpstarter_cli.run._serve_standalone).
        uniffi_set_event_loop(asyncio.get_running_loop())

        factory = DriverHostFactory.from_yaml(config_yaml)
        await serve_driver_host(uds, factory)

    anyio.run(serve)


if __name__ == "__main__":
    main(sys.argv[1:])

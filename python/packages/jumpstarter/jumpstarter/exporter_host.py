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
import sys

import anyio


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
    # The hub streams the single-entry config YAML on stdin (closed with EOF).
    config_yaml = sys.stdin.read()

    async def serve():
        import jumpstarter_core as jc

        from jumpstarter.exporter.host import DriverHostFactory

        # Foreign async callbacks (the Python driver methods Rust invokes) run on Rust/tokio
        # worker threads where asyncio.get_running_loop() raises; register this loop with UniFFI
        # so they schedule onto it (mirrors jumpstarter_cli.run._serve_standalone).
        set_event_loop = getattr(jc, "uniffi_set_event_loop", None)
        if set_event_loop is None:
            from jumpstarter_core import jumpstarter_core as _jc_mod

            set_event_loop = _jc_mod.uniffi_set_event_loop
        set_event_loop(asyncio.get_running_loop())

        factory = DriverHostFactory.from_yaml(config_yaml)
        await jc.serve_driver_host(uds, factory)

    anyio.run(serve)


if __name__ == "__main__":
    main(sys.argv[1:])

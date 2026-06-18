"""Entry point: ``python -m jumpstarter_exporter_host <exporter-config-path>``.

Loads the exporter config, builds a :class:`DriverHostFactory`, and awaits the Rust
``run_exporter`` for the process lifetime. ``jmp run`` re-execs into this so the exporter
runs in one process (Rust core + Python drivers via FFI), replacing the slim-host subprocess.
"""

import logging
import sys

import anyio

import jumpstarter_core as jc

from ._adapter import DriverHostFactory


async def _run(config_path: str) -> None:
    import asyncio

    # Register the asyncio loop with UniFFI so foreign async callbacks (driver_call,
    # stream_read, …) invoked from Rust/tokio worker threads schedule onto THIS loop
    # rather than calling asyncio.get_running_loop() on a non-loop thread (which raises
    # "no running event loop"). Required because the Rust core drives the exporter on
    # its own tokio threads while this loop is blocked awaiting run_exporter.
    # uniffi_set_event_loop is a module helper not re-exported by the package __init__,
    # so reach it via the inner module (maturin layout) with a flat-module fallback.
    set_event_loop = getattr(jc, "uniffi_set_event_loop", None)
    if set_event_loop is None:
        from jumpstarter_core import jumpstarter_core as _jc_mod

        set_event_loop = _jc_mod.uniffi_set_event_loop
    set_event_loop(asyncio.get_running_loop())

    factory = DriverHostFactory(config_path)
    await jc.run_exporter(config_path, factory)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m jumpstarter_exporter_host <exporter-config-path>", file=sys.stderr)
        raise SystemExit(2)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    anyio.run(_run, sys.argv[1])


if __name__ == "__main__":
    main()

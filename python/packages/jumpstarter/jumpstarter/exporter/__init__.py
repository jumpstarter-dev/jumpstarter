"""Exporter-side runtime.

The gRPC exporter (``Session``/``Exporter``) has been retired — the exporter now runs the
Rust core in-process via FFI: ``jmp run`` builds a
:class:`jumpstarter.exporter.host.DriverHostFactory` and awaits ``jumpstarter_core.run_exporter``.
What remains in this package is the in-process driver host (:mod:`jumpstarter.exporter.host`)
and driver logging (:mod:`jumpstarter.exporter.logging`).
"""

__all__: list[str] = []

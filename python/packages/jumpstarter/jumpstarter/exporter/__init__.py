"""Exporter-side runtime.

The gRPC exporter (``Session``/``Exporter``) has been retired — the exporter now runs as
the Rust core hosted in-process via FFI (``jmp run`` re-execs ``python -m
jumpstarter_exporter_host``). What remains here is the in-process driver host
(:mod:`jumpstarter.exporter.host`), driver logging, and the passphrase auth interceptors.
"""

__all__: list[str] = []

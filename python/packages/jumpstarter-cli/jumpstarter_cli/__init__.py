"""The thin Python `j` driver-client CLI that the native `j` binary delegates to
for Python-implemented driver clients (``python -m jumpstarter_cli.j``).

The controller/exporter transport (and its TLS) is handled by the Rust core via
the ``jumpstarter_core`` FFI client, so this package no longer injects system
certificates into Python's ``ssl`` module (the former ``truststore`` use).
"""

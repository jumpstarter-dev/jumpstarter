"""Jumpstarter — the thin Python driver/client library.

The client/exporter/controller runtime, the wire protocol and the value codec all
live in the Rust core (the ``jumpstarter_core`` extension). This package provides
only the driver-author surface: the ``Driver``/``DriverClient`` base classes and
the ``@export``/``@exportstream`` decorators.
"""

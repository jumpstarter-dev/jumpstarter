"""Traces of what an agent does through this server, in OpenTelemetry's terms.

The MCP library already makes a span of every tool call and takes up the trace an agent's host sends with it
(``traceparent`` in the request's ``_meta``), so a tool call is part of the agent's own trace. This module is what
this server adds:

* :func:`setup` sends the spans somewhere, when the person who starts the server asked for that in the standard way
  (``OTEL_EXPORTER_OTLP_ENDPOINT`` or ``OTEL_TRACES_EXPORTER``) and the OpenTelemetry SDK is installed
  (``jumpstarter-mcp[otel]``). Otherwise nothing is recorded, and nothing here costs anything.
* :func:`current` and :func:`within` carry the trace across a hand-over inside the server: a call that is passed on
  to another environment is made from another task, and would otherwise start a trace of its own.
* :func:`annotate` says on the current span what the call is about, in Jumpstarter's own words
  (``jumpstarter.*``): the conventions for agents' tools are still changing, so none of their names is relied on.

What is recorded is which tool was called on which lease, driver and environment, and how it went: never the
arguments, which may hold what is typed into a device.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

try:
    from opentelemetry import context as _context
    from opentelemetry import trace as _trace
except ImportError:  # the API comes with the MCP library; without it there is nothing to carry
    _context = None
    _trace = None

SERVICE_NAME = "jumpstarter-mcp"
#: The variables a person sets to have OpenTelemetry send traces: any of them asks for it.
ASKED = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_TRACES_EXPORTER")


def asked() -> bool:
    """Whether the person who started the server asked for traces, the standard way."""
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    if os.environ.get("OTEL_TRACES_EXPORTER", "").lower() == "none":
        return False
    return any(os.environ.get(name) for name in ASKED)


def setup(role: str = "server") -> bool:
    """Has the spans sent, when they were asked for and can be. Returns whether they are.

    ``role`` tells a server started by another (``worker``) from the one an agent's host started.
    """
    if not asked():
        return False
    try:
        import importlib

        # (asked of the import system by name: the SDK is an optional part, and may not be there to be checked)
        exporter = importlib.import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        resources = importlib.import_module("opentelemetry.sdk.resources")
        sdk = importlib.import_module("opentelemetry.sdk.trace")
        export = importlib.import_module("opentelemetry.sdk.trace.export")
    except ImportError:
        logger.warning(
            "Traces were asked for (OTEL_* is set) but the OpenTelemetry SDK is not installed here: "
            "install jumpstarter-mcp[otel]."
        )
        return False
    if _trace is None:
        return False
    resource = resources.Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", SERVICE_NAME), "jumpstarter.mcp.role": role}
    )
    provider = sdk.TracerProvider(resource=resource)
    provider.add_span_processor(export.BatchSpanProcessor(exporter.OTLPSpanExporter()))
    _trace.set_tracer_provider(provider)
    logger.info("traces are sent (%s)", role)
    return True


def current() -> Any:
    """The trace the caller is in, to hand to :func:`within` somewhere else. ``None`` without OpenTelemetry."""
    return _context.get_current() if _context is not None else None


@contextmanager
def within(carried: Any) -> Generator[None]:
    """Does what is inside as part of the trace that was carried here."""
    if _context is None or carried is None:
        yield
        return
    token = _context.attach(carried)
    try:
        yield
    finally:
        _context.detach(token)


def annotate(**attributes: Any) -> None:
    """Says on the current span what the call is about: ``annotate(tool="power_on")`` is ``jumpstarter.tool``.
    A value that is ``None`` is left out. Does nothing when no span is recorded."""
    if _trace is None:
        return
    span = _trace.get_current_span()
    for name, value in attributes.items():
        if value is not None:
            span.set_attribute(f"jumpstarter.{name.replace('__', '.')}", value)

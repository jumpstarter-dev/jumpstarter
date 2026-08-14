"""HTTP GET /metrics server for exporter-local Prometheus scrape."""

from __future__ import annotations

import logging
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

from .registry import MetricsRegistry, get_registry

logger = logging.getLogger(__name__)

ShutdownFunc = Callable[[], None]


def _parse_bind_addr(addr: str) -> tuple[str, int]:
    """Parse bind address forms: ':8080', '127.0.0.1:0', '8080', ':0'.

    When the host is omitted, default to loopback. Metrics endpoints are
    typically scraped via localhost/sidecar; use an explicit 0.0.0.0 host
    when remote scrape is required.
    """
    if ":" in addr:
        host, _, port_s = addr.rpartition(":")
        if host == "":
            host = "127.0.0.1"
    else:
        host = "127.0.0.1"
        port_s = addr
    return host, int(port_s)


def start_metrics_server(
    addr: str, registry: MetricsRegistry | None = None
) -> tuple[str, ShutdownFunc | None]:
    """Start an HTTP server exposing GET /metrics.

    Returns ``(listen_address, shutdown)``. When metrics are disabled or bind
    fails, returns ``(\"\", None)``.

    addr \"0\" or empty disables the server.
    addr ending with \":0\" binds an ephemeral port; the returned listen address
    is host:port suitable for urllib/http.Get.

    Bind failures (e.g. address already in use) and invalid bind addresses
    (non-numeric port) are non-fatal: a warning is logged and (\"\", None) is
    returned so the exporter can continue without metrics.

    Call ``shutdown()`` to stop the background server (no-op when None).
    """
    if addr == "" or addr == "0":
        return "", None

    reg = registry if registry is not None else get_registry()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.split("?", 1)[0] != "/metrics":
                self.send_error(404)
                return
            body = reg.generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return

    try:
        host, port = _parse_bind_addr(addr)
        server = ThreadingHTTPServer((host, port), Handler)
    except (OSError, ValueError) as e:
        logger.warning(
            "Failed to start metrics server for bind address %r (%s); continuing without /metrics",
            addr,
            e,
        )
        return "", None

    thread = Thread(target=server.serve_forever, name="jumpstarter-metrics", daemon=True)
    thread.start()

    def shutdown() -> None:
        server.shutdown()
        server.server_close()

    bound_host, bound_port = server.server_address[:2]
    # Prefer loopback-friendly host for ephemeral binds used in tests.
    if bound_host in ("0.0.0.0", "::", ""):
        bound_host = "127.0.0.1"
    return f"{bound_host}:{bound_port}", shutdown

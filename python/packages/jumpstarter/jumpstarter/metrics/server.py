"""HTTP GET /metrics server for exporter-local Prometheus scrape."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

from .registry import MetricsRegistry, get_registry


def _parse_bind_addr(addr: str) -> tuple[str, int]:
    """Parse bind address forms: ':8080', '127.0.0.1:0', '8080'."""
    if ":" in addr:
        host, _, port_s = addr.rpartition(":")
        if host == "":
            host = "0.0.0.0"
    else:
        host = "0.0.0.0"
        port_s = addr
    return host, int(port_s)


def start_metrics_server(addr: str, registry: MetricsRegistry | None = None) -> str:
    """Start an HTTP server exposing GET /metrics.

    addr \"0\" or empty disables the server and returns \"\".
    addr ending with \":0\" binds an ephemeral port; the returned listen address
    is host:port suitable for urllib/http.Get.
    """
    if addr == "" or addr == "0":
        return ""

    reg = registry if registry is not None else get_registry()
    host, port = _parse_bind_addr(addr)

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

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, name="jumpstarter-metrics", daemon=True)
    thread.start()

    bound_host, bound_port = server.server_address[:2]
    # Prefer loopback-friendly host for ephemeral binds used in tests.
    if bound_host in ("0.0.0.0", "::", ""):
        bound_host = "127.0.0.1"
    return f"{bound_host}:{bound_port}"

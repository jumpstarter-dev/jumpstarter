#!/usr/bin/env python3
"""Simulated cloud backend server for the mitmproxy demo.

Serves four REST endpoints that a DUT would typically call.
Every response includes a ``"source": "real-backend"`` field so the
difference between real and mocked traffic is immediately visible.

Usage::

    python backend.py [--port 9000]
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

START_TIME = time.time()

# ANSI colours for terminal output
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"


class DemoBackendHandler(BaseHTTPRequestHandler):
    """Handles GET/POST for the four demo API endpoints."""

    # Suppress the default stderr log line per request
    def log_message(self, format, *args):  # noqa: A002
        pass

    # ── routes ────────────────────────────────────────────────

    def do_GET(self):  # noqa: N802
        if self.path == "/api/v1/status":
            self._send_json(200, {
                "device_id": "DUT-REAL-001",
                "status": "online",
                "firmware_version": "1.0.0",
                "uptime_s": int(time.time() - START_TIME),
                "source": "real-backend",
            })
        elif self.path == "/api/v1/updates/check":
            self._send_json(200, {
                "update_available": False,
                "current_version": "1.0.0",
                "source": "real-backend",
            })
        elif self.path == "/api/v1/config":
            self._send_json(200, {
                "log_level": "info",
                "features": {
                    "ota_updates": True,
                    "remote_diagnostics": False,
                    "telemetry": True,
                },
                "source": "real-backend",
            })
        else:
            self._send_json(404, {
                "error": "not found",
                "source": "real-backend",
            })

    def do_POST(self):  # noqa: N802
        if self.path == "/api/v1/telemetry":
            # Read (and discard) the request body
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            self._send_json(200, {
                "accepted": True,
                "source": "real-backend",
            })
        else:
            self._send_json(404, {
                "error": "not found",
                "source": "real-backend",
            })

    # ── helpers ───────────────────────────────────────────────

    def _send_json(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self._log_request(status)

    def _log_request(self, status: int):
        colour = _GREEN if 200 <= status < 300 else _YELLOW
        ts = time.strftime("%H:%M:%S")
        print(
            f"  {_DIM}{ts}{_RESET}  "
            f"{colour}{status}{_RESET}  "
            f"{_CYAN}{self.command:4s}{_RESET} {self.path}"
        )


def main():
    parser = argparse.ArgumentParser(description="Demo backend server")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), DemoBackendHandler)
    print(f"Backend server listening on http://127.0.0.1:{args.port}")
    print("Endpoints:")
    print("  GET  /api/v1/status")
    print("  GET  /api/v1/updates/check")
    print("  POST /api/v1/telemetry")
    print("  GET  /api/v1/config")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()

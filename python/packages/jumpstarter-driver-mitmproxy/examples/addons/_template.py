"""
Custom addon template for jumpstarter-driver-mitmproxy.

Copy this file, rename it, and implement your Handler class.
The filename (without .py) becomes the addon name you reference
in mock scenario JSON files.

Example: Save as ``addons/my_custom_api.py``, then reference in
your scenario JSON as::

    "GET /my/endpoint/*": {
        "addon": "my_custom_api",
        "addon_config": {
            "any_key": "passed to your handler"
        }
    }

The Handler class must implement at minimum:

    handle(flow, config) -> bool
        Called for every matched HTTP request.
        Return True if you handled it (set flow.response).
        Return False to fall through to default handling.

Optional methods:

    websocket_message(flow, config)
        Called for each WebSocket message on matched connections.

    cleanup()
        Called when the addon is unloaded (not currently triggered
        automatically — reserved for future use).
"""

from __future__ import annotations

import json

from mitmproxy import ctx, http


class Handler:
    """Template handler — replace with your implementation."""

    def __init__(self):
        # Initialize any state your handler needs.
        # This is called once when the addon is first loaded.
        self.request_count = 0

    def handle(self, flow: http.HTTPFlow, config: dict) -> bool:
        """Handle an incoming HTTP request.

        Args:
            flow: The mitmproxy HTTPFlow. Read from flow.request,
                  write to flow.response.
            config: The "addon_config" dict from the mock scenario
                    JSON. Empty dict if not specified.

        Returns:
            True if you set flow.response (request is fully handled).
            False to let the request fall through to the real server
            or to the next matching mock rule.
        """
        self.request_count += 1

        # Example: return a JSON response
        flow.response = http.Response.make(
            200,
            json.dumps({
                "handler": "template",
                "request_count": self.request_count,
                "path": flow.request.path,
                "config": config,
            }).encode(),
            {"Content-Type": "application/json"},
        )

        ctx.log.info(
            f"Template handler: {flow.request.method} "
            f"{flow.request.path} (#{self.request_count})"
        )

        return True

    def websocket_message(self, flow: http.HTTPFlow, config: dict):
        """Handle a WebSocket message (optional).

        Args:
            flow: The HTTPFlow with .websocket data.
            config: The "addon_config" from the scenario JSON.
        """
        if flow.websocket is None:
            return

        last_msg = flow.websocket.messages[-1]
        if last_msg.from_client:
            ctx.log.info(
                f"WS client message: {last_msg.content!r}"
            )

            # Example: echo back to client with modification
            # ctx.master.commands.call(
            #     "inject.websocket", flow, True,
            #     b'{"type": "echo", "data": ...}',
            # )

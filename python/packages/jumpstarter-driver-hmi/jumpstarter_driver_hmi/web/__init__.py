"""The local web view of a hmi: ``j <hmi> view`` (see :mod:`jumpstarter_driver_hmi.web.server`)."""

from .server import create_app, serve_view

__all__ = ["create_app", "serve_view"]

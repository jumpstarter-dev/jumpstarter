"""
MCP decorators for driver clients.

Every public method of a client class becomes a tool of the Jumpstarter MCP
server automatically (see the ``jumpstarter-mcp`` package), as it becomes a
Robot Framework keyword and a ``j`` command. These decorators let a driver
author curate that, without making the driver depend on MCP::

    from jumpstarter.client.decorators import mcp

    class PowerClient(DriverClient):
        @mcp.tool(read_only=True)
        def read(self): ...

        @mcp.tool("cycle", description="Power the device off, wait, and on again.", destructive=True)
        def cycle(self, wait: int = 2): ...

        @mcp.not_tool
        def raw(self): ...

``read_only`` and ``destructive`` become the tool's annotations
(``readOnlyHint``, ``destructiveHint``): what an agent's host shows a person
before it lets the agent call the tool.
"""

import inspect
from collections.abc import Callable
from typing import Any, overload

from ._frontend import update_frontend_meta

FRONTEND = "mcp"


@overload
def tool[F: Callable[..., Any]](name: F) -> F: ...


@overload
def tool[F: Callable[..., Any]](
    name: str | None = None,
    description: str | None = None,
    read_only: bool | None = None,
    destructive: bool | None = None,
) -> Callable[[F], F]: ...


def tool(name=None, description=None, read_only=None, destructive=None):
    """Mark a client method as a tool, optionally naming and describing it.

    ``name`` replaces the method's name in the tool's name; the driver's tree
    path still comes first. ``description`` replaces the docstring. Works bare
    (``@mcp.tool``) or called.
    """
    if inspect.isroutine(name):
        return tool()(name)

    def decorator(func):
        update_frontend_meta(
            func,
            FRONTEND,
            name=name,
            description=description,
            read_only=read_only,
            destructive=destructive,
            hidden=False,
        )
        return func

    return decorator


def not_tool[F: Callable[..., Any]](func: F) -> F:
    """Keep a public client method out of the MCP server."""
    update_frontend_meta(func, FRONTEND, hidden=True)
    return func


@overload
def toolset[C: type](cls: C) -> C: ...


@overload
def toolset[C: type](*, auto_tools: bool = False, description: str | None = None) -> Callable[[C], C]: ...


def toolset(cls=None, *, auto_tools=False, description=None):
    """Configure how a client class is exposed, as ``robot.library`` does for keywords.

    Decorating a class turns automatic tools off by default, so only
    ``@mcp.tool`` methods are exposed; pass ``auto_tools=True`` to keep every
    public method. ``description`` replaces the class docstring as what the
    driver's tools are introduced with.
    """
    if inspect.isclass(cls):
        return toolset()(cls)

    def decorator(klass):
        update_frontend_meta(klass, FRONTEND, auto_tools=auto_tools, description=description)
        return klass

    return decorator


__all__ = ["not_tool", "tool", "toolset"]

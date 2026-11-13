"""
Per-frontend metadata attached to client methods and classes.

Client classes are exposed through several frontends: the ``j`` CLI, Robot
Framework keywords and the MCP server. Decorators for each frontend record
what they know about a method here, under the frontend's name, so every
frontend reads one store instead of each inventing its own attributes.
"""

from collections.abc import Mapping
from typing import Any

FRONTENDS_ATTR = "__jumpstarter_frontends__"


def frontend_meta(obj: Any, frontend: str) -> Mapping[str, Any]:
    """Return the metadata ``frontend`` recorded on ``obj``, empty if none."""
    store = getattr(obj, FRONTENDS_ATTR, None)
    if not isinstance(store, dict):
        return {}
    return dict(store.get(frontend, {}))


def update_frontend_meta(obj: Any, frontend: str, **values: Any) -> None:
    """Merge ``values`` into the metadata ``frontend`` keeps on ``obj``.

    Classes get their own store rather than mutating one inherited from a base
    class, so decorating a subclass never changes its parent.
    """
    store = obj.__dict__.get(FRONTENDS_ATTR) if isinstance(obj, type) else getattr(obj, FRONTENDS_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        setattr(obj, FRONTENDS_ATTR, store)
    store.setdefault(frontend, {}).update(values)

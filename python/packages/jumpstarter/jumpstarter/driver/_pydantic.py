"""Optional pydantic bridge — pydantic is NOT a runtime dependency of the core runtime.

Legacy drivers/clients that use pydantic message types pull pydantic in through their own
package dependencies; proto-first packages (generated dataclass models) don't need it at all.
When pydantic is absent, ``BaseModel`` here is a placeholder class nothing subclasses — every
``issubclass(x, BaseModel)`` check is simply ``False`` — and ``TypeAdapter`` is ``None``, so
the JSON-schema introspection fallback (already inside a ``try/except``) degrades to the
``Value`` mapping instead of crashing.
"""

try:
    from pydantic import BaseModel, TypeAdapter  # noqa: F401 — re-exported
except ImportError:  # pragma: no cover — exercised by pydantic-free installs
    TypeAdapter = None  # type: ignore[assignment,misc]

    class BaseModel:  # type: ignore[no-redef]
        """Placeholder: no real type subclasses this, so issubclass checks are False."""

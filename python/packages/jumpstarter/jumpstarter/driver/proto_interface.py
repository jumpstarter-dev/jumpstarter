"""Runtime base for codegen-generated proto-first driver interfaces.

A generated interface base (e.g. ``PowerInterface`` generated from ``power.proto``) subclasses
:class:`ProtoInterface`, so a native driver author subclasses ONLY the generated base::

    from ._generated.power_driver import PowerInterface

    class MyPower(PowerInterface):
        async def on(self) -> None: ...

No ``Driver`` superclass and no ``@export`` decorators: :class:`ProtoInterface` IS a ``Driver``
(the generated base carries the full driver machinery), and implementations of the interface's
declared RPC methods are exported automatically at class creation — the Python analog of Rust's
``#[driver]`` on an ``impl PowerInterface`` block and the JVM's ``@JumpstarterDriver`` service
subclass. The generated base advertises its generated typed client by default; point a driver at
a custom client with :func:`jumpstarter.driver.decorators.driver`. ``@export`` remains the
surface for legacy hand-written drivers (and is harmless, if redundant, on interface methods
here).
"""

import inspect

from .base import Driver
from .decorators import export


class ProtoInterface(Driver):
    """Base of every generated proto-first interface class (see jumpstarter-codegen)."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Auto-export: a concrete implementation of any RPC method declared (abstract) by a
        # generated interface base in the MRO gets the same markers @export would apply.
        declared: set[str] = set()
        for base in cls.__mro__[1:]:
            if base is not ProtoInterface and issubclass(base, ProtoInterface):
                declared.update(getattr(base, "__abstractmethods__", ()))
        for name in declared:
            impl = cls.__dict__.get(name)
            if impl is not None and inspect.isfunction(impl):
                export(impl)

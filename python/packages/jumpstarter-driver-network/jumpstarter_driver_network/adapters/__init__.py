# Lazy re-exports (PEP 562). Importing this package eagerly pulled every adapter's heavy optional
# deps — most notably `FabricAdapter`, which drags in fabric/paramiko (SSH, ~60ms) — even when a
# client only uses the lightweight port-forward/dbus adapters. That tax landed on *every* client
# build (e.g. a `j power on` that touches an EchoNetwork driver). Resolve each adapter to its
# submodule only on first access, so the import cost is paid by the code that actually uses it.

import importlib

_ADAPTERS = {
    "DbusAdapter": ".dbus",
    "FabricAdapter": ".fabric",
    "NovncAdapter": ".novnc",
    "PexpectAdapter": ".pexpect",
    "TcpPortforwardAdapter": ".portforward",
    "UnixPortforwardAdapter": ".portforward",
}

__all__ = list(_ADAPTERS)


def __getattr__(name: str):
    module = _ADAPTERS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module, __name__), name)


def __dir__():
    return sorted(__all__)

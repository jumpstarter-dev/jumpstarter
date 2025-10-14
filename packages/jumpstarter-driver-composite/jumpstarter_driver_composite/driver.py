from dataclasses import field
from functools import reduce

from pydantic.dataclasses import dataclass

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


class CompositeInterface:
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_composite.client.CompositeClient"


@dataclass(kw_only=True)
class Composite(CompositeInterface, Driver):
    pass


@dataclass(kw_only=True)
class Proxy(Driver):
    ref: str
    _proxy_target: Driver | None = field(default=None, init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        raise NotImplementedError("Proxy.client() should never be called; report() delegates to target")

    def _resolve_proxy_target(self, root, name):
        if self._proxy_target:
            return self._proxy_target
        try:
            path = self.ref.split(".")
            if not path:
                raise ConfigurationError(f"Proxy driver {name} has empty path")
            self._proxy_target = reduce(lambda instance, name: instance.children[name], path, root)
            return self._proxy_target
        except KeyError:
            raise ConfigurationError(f"Proxy driver {name} references nonexistent driver {self.ref}") from None

    def report(self, *, parent=None, name=None):
        if not self._proxy_target:
            raise RuntimeError("Proxy target not resolved. Call enumerate() before report()")
        return self._proxy_target.report(parent=parent, name=name)

    def enumerate(self, *, root=None, parent=None, name=None):
        return self._resolve_proxy_target(root or self, name).enumerate(root=root or self, parent=parent, name=name)

    def __getattr__(self, name):
        if not self._proxy_target:
            raise RuntimeError(f"Proxy target not resolved. Call enumerate() before accessing '{name}'")
        return getattr(self._proxy_target, name)

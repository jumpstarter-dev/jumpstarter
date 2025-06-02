from dataclasses import dataclass

from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class ShellClient(DriverClient):
    _methods: list[str] | None = None

    """
    Client interface for Shell driver.

    This client dynamically checks that the method is configured
    on the driver, and if it is, it will call it and get the results
    in the form of (stdout, stderr, returncode).
    """

    def _check_method_exists(self, method):
        if self._methods is None:
            self._methods = self.call("get_methods")
        if method not in self._methods:
            raise AttributeError(f"method {method} not found in {self._methods}")

    ## capture any method calls dynamically
    def __getattr__(self, name):
        self._check_method_exists(name)
        return lambda *args, **kwargs: tuple(self.call("call_method", name, kwargs, *args))

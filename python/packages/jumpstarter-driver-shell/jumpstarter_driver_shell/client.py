import logging
import sys
from dataclasses import dataclass

import click
import click.exceptions

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ShellClient(DriverClient):
    _methods: set[str] | None = None

    """
    Client interface for Shell driver.

    This client dynamically checks that the method is configured
    on the driver, and if it is, it will call it with live streaming output.
    Output chunks are displayed as they arrive.
    """

    def _check_method_exists(self, method):
        if self._methods is None:
            self._methods = set(self.call("get_methods"))
        if method not in self._methods:
            raise AttributeError(f"method {method} not found in {self._methods}")

    def _call_method(self, method_name, *args, **kwargs):
        self._check_method_exists(method_name)
        returncode = 0
        for stdout, stderr, code in self.streamingcall("call_method", method_name, kwargs, *args):
            if stdout:
                print(stdout, end='', flush=True)
            if stderr:
                print(stderr, end='', file=sys.stderr, flush=True)
            if code is not None:
                returncode = code
        return returncode

    def __getattribute__(self, name):
        if not name.startswith("_"):
            d = object.__getattribute__(self, "__dict__")
            methods = d.get("_methods")

            # Lazy-load on first access; guard prevents recursion
            # since self.call() re-enters __getattribute__
            if methods is None and not d.get("_loading_methods"):
                d["_loading_methods"] = True
                try:
                    methods = set(object.__getattribute__(self, "call")("get_methods"))
                    d["_methods"] = methods
                except Exception:
                    logger.debug("Failed to lazy-load shell methods", exc_info=True)
                finally:
                    d.pop("_loading_methods", None)

            if methods and name in methods:
                return lambda *args, **kwargs: object.__getattribute__(self, "_call_method")(name, *args, **kwargs)

        return object.__getattribute__(self, name)

    def __getattr__(self, name):
        self._check_method_exists(name)
        def execute(*args, **kwargs):
            return self._call_method(name, *args, **kwargs)
        return execute

    def cli(self):
        """Create CLI interface for dynamically configured shell methods"""
        @driver_click_group(self)
        def base():
            """Shell command executor"""
            pass

        # Get available methods from the driver
        if self._methods is None:
            self._methods = set(self.call("get_methods"))

        # Create a command for each configured method
        for method_name in self._methods:
            self._add_method_command(base, method_name)

        return base

    def _add_method_command(self, group, method_name):
        """Add a Click command for a specific shell method"""
        def method_command(args, env):
            # Parse environment variables
            env_dict = {}
            for env_var in env:
                if '=' in env_var:
                    key, value = env_var.split('=', 1)
                    env_dict[key] = value
                else:
                    raise click.BadParameter(f"Invalid --env value '{env_var}'. Use KEY=VALUE.")

            returncode = self._call_method(method_name, *args, **env_dict)

            # Exit with the same return code as the shell command
            if returncode != 0:
                raise click.exceptions.Exit(returncode)

        # Decorate and register the command with help text
        method_command = click.argument('args', nargs=-1, type=click.UNPROCESSED)(method_command)
        method_command = click.option('--env', '-e', multiple=True,
                     help='Environment variables in KEY=VALUE format')(method_command)
        method_command = group.command(
            name=method_name,
            help=self.methods_description.get( method_name, f"Execute the {method_name} shell method"),
            context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
        )(method_command)

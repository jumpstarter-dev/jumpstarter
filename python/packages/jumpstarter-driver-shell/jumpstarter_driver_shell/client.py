import sys
from dataclasses import dataclass

import click

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class ShellClient(DriverClient):
    _methods: list[str] | None = None

    """
    Client interface for Shell driver.

    This client dynamically checks that the method is configured
    on the driver, and if it is, it will call it with live streaming output.
    Output chunks are displayed as they arrive.
    """

    def _check_method_exists(self, method):
        if self._methods is None:
            self._methods = self.call("get_methods")
        if method not in self._methods:
            raise AttributeError(f"method {method} not found in {self._methods}")

    ## capture any method calls dynamically
    def __getattr__(self, name):
        self._check_method_exists(name)
        def execute(*args, **kwargs):
            returncode = 0
            for stdout, stderr, code in self.streamingcall("call_method", name, kwargs, *args):
                if stdout:
                    print(stdout, end='', flush=True)
                if stderr:
                    print(stderr, end='', file=sys.stderr, flush=True)
                if code is not None:
                    returncode = code
            return returncode
        return execute

    def cli(self):
        """Create CLI interface for dynamically configured shell methods"""
        @driver_click_group(self)
        def base():
            """Shell command executor"""
            pass

        # Get available methods from the driver
        if self._methods is None:
            self._methods = self.call("get_methods")

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

            returncode = getattr(self, method_name)(*args, **env_dict)

            # Exit with the same return code as the shell command
            if returncode != 0:
                raise click.exceptions.Exit(returncode)

        # Try to get custom description, fall back to default for older than 0.7 servers
        try:
            description = self.call("get_method_description", method_name)
        except Exception:
            description = f"Execute the {method_name} shell method"

        # Decorate and register the command with help text
        method_command = click.argument('args', nargs=-1, type=click.UNPROCESSED)(method_command)
        method_command = click.option('--env', '-e', multiple=True,
                     help='Environment variables in KEY=VALUE format')(method_command)
        method_command = group.command(
            name=method_name,
            help=description,
            context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
        )(method_command)

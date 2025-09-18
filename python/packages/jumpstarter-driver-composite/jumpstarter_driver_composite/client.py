from dataclasses import dataclass
import inspect

import click

from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class CompositeClient(DriverClient):
    def __getattr__(self, name):
        return self.children[name]

    def close(self):
        for _, v in self.children.items():
            if hasattr(v, "close"):
                v.close()

    def cli(self):
        @click.group
        def base():
            """Generic composite device"""
            pass

        for k, v in self.children.items():
            if hasattr(v, "cli"):
                # Check if the cli method accepts a click_group parameter
                sig = inspect.signature(v.cli)
                if "click_group" in sig.parameters:
                    base.add_command(v.cli(click_group=base), k)
                else:
                    base.add_command(v.cli(), k)

        return base

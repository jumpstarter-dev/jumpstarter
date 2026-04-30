from typing import Callable

import click
from click.shell_completion import get_completion_class


def make_completion_command(cli_group_factory: Callable[[], click.Command], prog_name: str, complete_var: str):
    @click.command("completion")
    @click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
    def completion(shell: str):
        """Generate shell completion script."""
        cli_group = cli_group_factory()
        comp_cls = get_completion_class(shell)
        if comp_cls is None:
            raise click.ClickException(f"Unsupported shell: {shell}")
        comp = comp_cls(cli_group, {}, prog_name, complete_var)
        click.echo(comp.source())

    return completion

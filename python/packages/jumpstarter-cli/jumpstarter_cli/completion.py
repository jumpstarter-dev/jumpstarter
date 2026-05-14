import click
from click.shell_completion import get_completion_class


@click.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str):
    """Generate shell completion script."""
    from jumpstarter_cli.jmp import jmp

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")
    comp = comp_cls(jmp, {}, "jmp", "_JMP_COMPLETE")
    click.echo(comp.source())

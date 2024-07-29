import click


class AliasedGroup(click.Group):
    """An aliased command group."""

    common_aliases: dict[str, list[str]] = {
        "remove": ["rm"],
        "list": ["ls"],
        "create": ["cr"],
        "move": ["mv"],
        "config": ["conf"],
        "delete": ["del"]
    }

    def get_command(self, ctx: click.Context, cmd_name: str):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        # Match if listed in the common aliases
        matches = [x for x in self.list_commands(ctx) if self.common_aliases[x] and cmd_name in self.common_aliases[x]]
        if not matches:
            return None
        elif len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Too many matches: {', '.join(sorted(matches))}")

    def resolve_command(self, ctx, args):
        # always return the full command name
        _, cmd, args = super().resolve_command(ctx, args)
        return cmd.name, cmd, args

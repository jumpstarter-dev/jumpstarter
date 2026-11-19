import re

import click

_RST_CODE_BLOCK = re.compile(r'^\s*\.\. code-block::')


def _rst_to_click(help_text: str) -> str:
    lines = help_text.splitlines()
    result = []
    i = 0
    while i < len(lines):
        if _RST_CODE_BLOCK.match(lines[i]):
            i += 1
            if i < len(lines) and not lines[i].strip():
                i += 1
            result.append('\b')
        else:
            result.append(lines[i])
            i += 1
    return '\n'.join(result)


class RSTStrippingCommand(click.Command):
    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if self.help:
            formatter.write_paragraph()
            with formatter.indentation():
                formatter.write_text(_rst_to_click(self.help))

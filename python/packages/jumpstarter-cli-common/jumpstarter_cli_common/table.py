from io import StringIO
from typing import cast

from rich.console import Console
from rich.table import Table


def make_table(columns: list[str], values: list[dict]):
    """Print a pretty table from a list of `columns` and a list of `values`, each of which is a valid `dict`"""

    table = Table(
        box=None,
        header_style=None,
        pad_edge=False,
    )

    for name in columns:
        table.add_column(
            name,
            overflow="fold",
            no_wrap=(name == "UUID"),
        )

    for v in values:
        table.add_row(*[v[k] for k in columns])

    console = Console(file=StringIO())
    console.print(table)
    return cast(StringIO, console.file).getvalue()

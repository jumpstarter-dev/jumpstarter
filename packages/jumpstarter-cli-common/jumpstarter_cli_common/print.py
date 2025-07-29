import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from .opt import OutputMode, OutputType


def model_print(  # noqa: C901
    model: BaseModel,
    output: OutputType,
    namespace: str | None = None,
    **kwargs,
):
    console = Console()

    match output:
        case OutputMode.JSON:
            console.print_json(
                data=model.model_dump_json(
                    by_alias=True,
                ),
                indent=4,
            )
        case OutputMode.YAML:
            console.print(
                yaml.safe_dump(
                    model.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                    indent=2,
                )
            )
        case OutputMode.NAME:
            names = []

            try:
                model.rich_add_names(names)
            except AttributeError as err:
                raise NotImplementedError from err

            for name in names:
                console.print(name)
        case OutputMode.PATH:
            paths = []

            try:
                model.rich_add_paths(paths)
            except AttributeError as err:
                raise NotImplementedError from err

            for path in paths:
                console.print(path)
        case _:
            table = Table(
                box=None,
                header_style=None,
                pad_edge=None,
            )

            try:
                model.rich_add_columns(table, **kwargs)
                model.rich_add_rows(table, **kwargs)
            except AttributeError as err:
                raise NotImplementedError from err

            if len(table.rows) == 0:
                if namespace:
                    console.print("No resources found in {} namespace.".format(namespace))
                else:
                    console.print("No resources found.")
            else:
                console.print(table)

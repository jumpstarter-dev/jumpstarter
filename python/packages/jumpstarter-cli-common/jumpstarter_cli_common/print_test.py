import pytest
from pydantic import BaseModel
from rich.table import Table

from .opt import OutputMode
from .print import model_print


class _Exporters(BaseModel):
    names: list[str]

    def rich_add_names(self, names: list[str]) -> None:
        names.extend(f"exporter/{name}" for name in self.names)

    def rich_add_paths(self, paths: list[str]) -> None:
        paths.extend(f"exporters/{name}" for name in self.names)

    def rich_add_columns(self, table: Table, devices: bool = False) -> None:
        table.add_column("NAME")
        if devices:
            table.add_column("DEVICES")

    def rich_add_rows(self, table: Table, devices: bool = False) -> None:
        for name in self.names:
            cells = [name, f"{name}-device"] if devices else [name]
            table.add_row(*cells)


class _NotPrintable(BaseModel):
    value: int = 0


def test_name_output_prints_each_name_on_its_own_line(capsys):
    model_print(_Exporters(names=["a", "b"]), OutputMode("name"))

    assert capsys.readouterr().out == "exporter/a\nexporter/b\n"


def test_path_output_prints_each_path_on_its_own_line(capsys):
    model_print(_Exporters(names=["a", "b"]), OutputMode("path"))

    assert capsys.readouterr().out == "exporters/a\nexporters/b\n"


def test_table_output_passes_options_to_the_columns_and_the_rows(capsys):
    model_print(_Exporters(names=["a"]), None, devices=True)

    assert capsys.readouterr().out.split() == ["NAME", "DEVICES", "a", "a-device"]


def test_table_output_without_rows_names_the_empty_namespace(capsys):
    model_print(_Exporters(names=[]), None, namespace="lab")

    assert capsys.readouterr().out == "No resources found in lab namespace.\n"


@pytest.mark.parametrize("output", [OutputMode("name"), OutputMode("path"), None])
def test_models_without_rich_output_support_are_rejected(output):
    with pytest.raises(NotImplementedError):
        model_print(_NotPrintable(), output)

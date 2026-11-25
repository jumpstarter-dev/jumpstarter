"""Signatures of a fake client written the way real clients are: postponed annotations."""

from __future__ import annotations

import dataclasses
import enum
import os
import typing
from typing import Literal

import pytest
from pydantic import BaseModel
from robot.api import Failure

from .signatures import argument_spec, robot_type, source_of

PathBuf = str | os.PathLike


class Operator:
    """Stands in for opendal.Operator: a class Robot Framework cannot convert."""


class Mode(enum.Enum):
    FAST = "fast"
    SLOW = "slow"


class Options(BaseModel):
    retries: int = 1


@dataclasses.dataclass
class Point:
    x: int
    y: int = 0


class FakeClient:
    def flash(self, path: PathBuf, *, target: str | None = None, operator: Operator | None = None) -> None: ...

    def choose(self, mode: Mode, speed: Literal["low", "high"] = "low") -> None: ...

    def var(self, first: int, *rest: int, flag: bool = True, **env: str) -> None: ...

    def configure(self, options: Options, maybe: Options | None = None) -> None: ...

    def move(self, to: Point) -> None: ...

    def untyped(self, a, b=2): ...


def spec(name: str):
    return argument_spec(getattr(FakeClient, name))


def test_robot_type():
    assert robot_type(int) is int
    assert robot_type(Mode) is Mode
    assert robot_type(list[int]) == list[int]
    assert robot_type(int | None) == typing.Union[int, None]  # noqa: UP007
    assert robot_type(PathBuf) is str
    # classes Robot Framework cannot convert are left untyped, alone or in a union
    assert robot_type(Operator) is None
    assert robot_type(str | Operator) is None


def test_arguments_defaults_and_types():
    flash = spec("flash")
    assert flash.args == ["path", "*", ("target", None), ("operator", None)]
    assert flash.types == {"path": str, "target": typing.Union[str, None]}  # noqa: UP007
    choose = spec("choose")
    assert choose.args == ["mode", ("speed", "low")]
    assert choose.types == {"mode": Mode, "speed": Literal["low", "high"]}
    assert spec("untyped").args == ["a", ("b", 2)]


def test_var_args_and_kwargs():
    var = spec("var")
    assert var.args == ["first", "*rest", ("flag", True), "**env"]
    assert var.types == {"first": int, "rest": int, "flag": bool, "env": str}


def test_models_are_built_from_json_or_dicts():
    configure = spec("configure")
    assert "options" not in configure.types
    assert configure.bind(['{"retries": 3}'], {}) == ([Options(retries=3)], {})
    assert configure.bind([], {"options": {"retries": 2}, "maybe": None}) == ([Options(retries=2), None], {})
    assert spec("move").bind(['{"x": 1, "y": 2}'], {}) == ([Point(1, 2)], {})


def test_invalid_model_value_names_the_argument():
    with pytest.raises(Failure, match="Invalid value for argument 'options'"):
        spec("configure").bind(["not json"], {})


def test_source():
    source = source_of(FakeClient.flash)
    assert source is not None and source.rpartition(":")[0].endswith("signatures_test.py")
    assert source_of(len) is None

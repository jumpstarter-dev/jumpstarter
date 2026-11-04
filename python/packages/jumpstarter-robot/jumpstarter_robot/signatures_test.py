"""Signatures of a fake client written the way real clients are: postponed annotations."""

from __future__ import annotations

import dataclasses
import datetime
import enum
import os
import pathlib
import typing
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, Literal

import pytest
from pydantic import BaseModel
from robot.api import Failure

from .signatures import argument_spec, resolve_hints, robot_type, source_of

if TYPE_CHECKING:
    from opendal import Operator as TypeCheckingOnly  # never imported at run time

PathBuf = str | os.PathLike


class Operator:
    """Stands in for opendal.Operator: a class Robot Framework cannot convert."""


class Mode(enum.Enum):
    FAST = "fast"
    SLOW = "slow"


class Options(BaseModel):
    retries: int = 1
    name: str = "x"


@dataclasses.dataclass
class Point:
    x: int
    y: int = 0


_SENTINEL = object()


class FakeClient:
    def flash(self, path: PathBuf, *, target: str | None = None, operator: Operator | None = None) -> None: ...

    def copy(self, src: str | Operator, dst: PathBuf | Operator) -> None: ...

    def write(self, data: PathBuf | None = None, operator: Operator | dict[str, Operator] | None = None) -> None: ...

    def level(self, cfg: int | Options) -> None: ...

    def choose(self, mode: Mode, speed: Literal["low", "high"] = "low") -> None: ...

    def configure(self, options: Options, maybe: Options | None = None) -> None: ...

    def move(self, to: Point) -> None: ...

    def positional(self, a: int, b: str, /, c: float = 1.5, *, d: bool = False) -> None: ...

    def var(self, first: int, *rest: int, flag: bool = True, **env: str) -> None: ...

    def keyword_only(self, *, timeout: datetime.timedelta, sentinel: object = _SENTINEL) -> None: ...

    def hidden_hint(self, op: TypeCheckingOnly, count: int = 3) -> None: ...

    def containers(self, items: list[int], mapping: dict[str, Operator], pair: tuple[int, ...]) -> None: ...

    def paths(self, p: pathlib.Path, q: os.PathLike) -> None: ...

    def untyped(self, a, b=2): ...

    def anything(self, value: Any) -> None: ...

    def stream(self, count: int = 1) -> Generator[int, None, None]:
        yield count


def spec(name: str):
    return argument_spec(getattr(FakeClient, name))


class TestRobotType:
    def test_scalars_pass_through(self):
        for tp in (str, int, float, bool, bytes, pathlib.Path, datetime.timedelta, type(None)):
            assert robot_type(tp) is tp

    def test_any_and_unknown_classes_are_untyped(self):
        assert robot_type(Any) is None
        assert robot_type(Operator) is None
        assert robot_type(Options) is None

    def test_pathbuf_collapses_to_str(self):
        assert robot_type(PathBuf) is str

    def test_unions_with_an_unconvertible_member_are_untyped(self):
        # Robot would reject an Operator, or turn it into text, if told the type is str
        assert robot_type(str | Operator) is None
        assert robot_type(Operator | None) is None
        assert robot_type(int | str | Operator) is None
        assert robot_type(int | Options) is None
        assert robot_type(PathBuf | Operator) is None
        assert robot_type(Operator | dict[str, Operator] | None) is None

    def test_optional_pathbuf_stays_optional_str(self):
        assert robot_type(PathBuf | None) == typing.Union[str, None]  # noqa: UP007
        assert robot_type(os.PathLike | None) is None  # no str to collapse into

    def test_optional_keeps_none(self):
        assert robot_type(int | None) == typing.Union[int, None]  # noqa: UP007

    def test_literal_and_enum(self):
        assert robot_type(Literal["a", "b"]) == Literal["a", "b"]
        assert robot_type(Mode) is Mode

    def test_containers(self):
        assert robot_type(list[int]) == list[int]
        assert robot_type(dict[str, Operator]) is dict
        assert robot_type(tuple[int, ...]) == tuple[int, ...]
        assert robot_type(list) is list

    def test_pathlike(self):
        assert robot_type(os.PathLike) is None
        assert robot_type(os.PathLike[str]) is None


class TestArgumentSpec:
    def test_pathbuf_and_operator(self):
        s = spec("flash")
        assert s.args == ["path", "*", ("target", None), ("operator", None)]
        assert s.types == {"path": str, "target": typing.Union[str, None]}  # noqa: UP007
        assert s.coercers == {}

    def test_operator_unions(self):
        s = spec("copy")
        assert s.types == {}
        assert spec("write").types == {"data": typing.Union[str, None]}  # noqa: UP007

    def test_robot_passes_unconvertible_union_values_unchanged(self):
        from robot.api import TypeInfo

        operator = Operator()
        for method, param in (("copy", "src"), ("copy", "dst"), ("write", "operator")):
            hint = spec(method).types.get(param)
            if hint is not None:  # what Robot Framework does with an argument of a reported type
                assert TypeInfo.from_type_hint(hint).convert(operator, param) is operator

    def test_enum_and_literal(self):
        s = spec("choose")
        assert s.args == ["mode", ("speed", "low")]
        assert s.types == {"mode": Mode, "speed": Literal["low", "high"]}

    def test_positional_only_and_keyword_only(self):
        s = spec("positional")
        assert s.args == ["a", "b", "/", ("c", 1.5), "*", ("d", False)]
        assert s.types == {"a": int, "b": str, "c": float, "d": bool}

    def test_var_args_and_kwargs(self):
        s = spec("var")
        assert s.args == ["first", "*rest", ("flag", True), "**env"]
        assert s.types == {"first": int, "rest": int, "flag": bool, "env": str}

    def test_keyword_only_without_varargs(self):
        s = spec("keyword_only")
        assert s.args[0] == "*"
        assert s.args[1] == "timeout"
        assert s.types == {"timeout": datetime.timedelta}

    def test_defaults_are_real_objects(self):
        s = spec("keyword_only")
        assert s.args[2] == ("sentinel", _SENTINEL)
        assert s.args[2][1] is _SENTINEL
        assert spec("untyped").args == ["a", ("b", 2)]
        assert spec("untyped").args[1][1] == 2

    def test_type_checking_only_hint_falls_back(self):
        # get_type_hints fails on the TYPE_CHECKING-only name; the other hints resolve
        hints = resolve_hints(FakeClient.hidden_hint)
        assert "op" not in hints
        assert hints["count"] is int
        s = spec("hidden_hint")
        assert s.args == ["op", ("count", 3)]
        assert s.types == {"count": int}

    def test_containers(self):
        s = spec("containers")
        assert s.types == {"items": list[int], "mapping": dict, "pair": tuple[int, ...]}

    def test_paths(self):
        assert spec("paths").types == {"p": pathlib.Path}

    def test_any_is_untyped(self):
        assert spec("anything").types == {}

    def test_skip(self):
        assert argument_spec(FakeClient.choose, skip=2).args == [("speed", "low")]
        assert argument_spec(lambda a, b: None, skip=0).args == ["a", "b"]

    def test_self_positional_only(self):
        def method(self, /, a: int): ...

        assert argument_spec(method).args == ["a"]

    def test_source(self):
        source = source_of(FakeClient.flash)
        assert source is not None
        path, _, line = source.rpartition(":")
        assert path.endswith("signatures_test.py")
        assert int(line) > 0
        assert source_of(len) is None


class TestCoercion:
    def test_pydantic_from_json_string(self):
        s = spec("configure")
        assert set(s.coercers) == {"options", "maybe"}
        assert "options" not in s.types
        args, kwargs = s.bind(['{"retries": 3}'], {})
        assert args == [Options(retries=3)]
        assert kwargs == {}

    def test_pydantic_from_dict_and_named(self):
        s = spec("configure")
        args, kwargs = s.bind([], {"options": {"name": "y"}, "maybe": '{"retries": 2}'})
        # named arguments come back positionally, as inspect binds them
        assert args == [Options(name="y"), Options(retries=2)]
        assert kwargs == {}

    def test_optional_model_accepts_none(self):
        s = spec("configure")
        args, _ = s.bind([{"retries": 1}, None], {})
        assert args == [Options(), None]

    def test_model_instance_passes(self):
        s = spec("configure")
        model = Options(retries=9)
        args, _ = s.bind([model], {})
        assert args[0] == model

    def test_union_with_a_model(self):
        s = spec("level")
        assert s.types == {}
        assert set(s.coercers) == {"cfg"}
        assert s.bind(['{"retries": 4}'], {})[0] == [Options(retries=4)]
        assert s.bind(["5"], {})[0] == [5]

    def test_dataclass(self):
        s = spec("move")
        assert set(s.coercers) == {"to"}
        args, _ = s.bind(['{"x": 1, "y": 2}'], {})
        assert args == [Point(1, 2)]
        args, _ = s.bind([{"x": 5}], {})
        assert args == [Point(5)]

    def test_invalid_value_fails_with_argument_name(self):
        s = spec("configure")
        with pytest.raises(Failure, match="Invalid value for argument 'options'"):
            s.bind(['{"retries": "many"}'], {})
        with pytest.raises(Failure, match="'options'"):
            s.bind(["not json"], {})

    def test_bind_without_coercers_is_a_copy(self):
        s = spec("choose")
        args, kwargs = s.bind(["fast"], {"speed": "high"})
        assert args == ["fast"]
        assert kwargs == {"speed": "high"}

    def test_bind_with_var_kwargs(self):
        class Client:
            def run(self, options: Options, *args: str, **env: str): ...

        s = argument_spec(Client.run)
        args, kwargs = s.bind(['{"retries": 4}', "a", "b"], {"X": "1"})
        assert args == [Options(retries=4), "a", "b"]
        assert kwargs == {"X": "1"}

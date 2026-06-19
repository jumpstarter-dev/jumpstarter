"""Contract tests for ``to_jsonable`` — the FFI value-boundary normalizer.

Expected values are hardcoded (not derived from pydantic) so the contract is pinned
independently of the legacy codec; they were verified byte-for-byte against
``pydantic.TypeAdapter(Any).dump_python(value, mode="json")`` when authored.
"""

import math
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import PurePosixPath
from uuid import UUID

import pytest

from jumpstarter.common.jsonable import to_jsonable

_UUID = UUID("12345678-1234-5678-1234-567812345678")


class Color(Enum):
    RED = "red"


class Num(IntEnum):
    ONE = 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (False, False),
        (0, 0),
        (-5, -5),
        ("hi", "hi"),
        (3.5, 3.5),
        (b"hello", "hello"),
        (bytearray(b"ba"), "ba"),
        (_UUID, "12345678-1234-5678-1234-567812345678"),
        (Color.RED, "red"),
        (Num.ONE, 1),
        (datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "2024-01-02T03:04:05Z"),
        (datetime(2024, 1, 2, 3, 4, 5), "2024-01-02T03:04:05"),
        (date(2024, 1, 2), "2024-01-02"),
        (time(3, 4, 5), "03:04:05"),
        (Decimal("1.50"), "1.50"),
        (PurePosixPath("/tmp/x"), "/tmp/x"),
        ((1, 2, 3), [1, 2, 3]),
        (frozenset({4}), [4]),
        ({"k": Color.RED, "n": [Decimal("2.5"), b"z"]}, {"k": "red", "n": ["2.5", "z"]}),
    ],
)
def test_to_jsonable(value, expected):
    assert to_jsonable(value) == expected


def test_non_finite_floats_become_none():
    assert to_jsonable(float("nan")) is None
    assert to_jsonable(float("inf")) is None
    assert to_jsonable(float("-inf")) is None


def test_set_becomes_list():
    assert sorted(to_jsonable({1, 2, 3})) == [1, 2, 3]


def test_non_string_dict_keys_stringified():
    assert to_jsonable({1: "a"}) == {"1": "a"}


def test_pydantic_model_uses_model_dump():
    pydantic = pytest.importorskip("pydantic")

    class M(pydantic.BaseModel):
        a: int = 3
        u: UUID = _UUID

    assert to_jsonable(M()) == {"a": 3, "u": "12345678-1234-5678-1234-567812345678"}


def test_unserializable_raises():
    class Weird:
        pass

    with pytest.raises(TypeError):
        to_jsonable(Weird())


def test_finite_float_preserved():
    assert to_jsonable(1.25) == 1.25
    assert math.isclose(to_jsonable(-0.001), -0.001)

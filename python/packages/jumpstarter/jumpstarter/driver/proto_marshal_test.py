"""Tests for proto_marshal — the proto ⇄ native marshaller (inverse of descriptor_builder).

Each test defines a concrete ``Driver`` with ``@export`` methods, builds the marshaller from it, and
round-trips a category of types through real protobuf message classes. Because the pool is built the
same way the exporter host advertises it, a failure here flags drift between the forward
(descriptor_builder/type_mapping) and the inverse (proto_marshal) directions.

NOTE: this module intentionally does NOT ``from __future__ import annotations`` — the descriptor
pipeline introspects real runtime annotations (as shipped drivers write them), and stringizing them
would detour through the lossy Pydantic-JSON-schema fallback instead of the direct type map.
"""

import dataclasses
import enum
from uuid import UUID, uuid4

import pytest
from google.protobuf.json_format import ParseDict
from pydantic import BaseModel

from jumpstarter.driver import Driver, export
from jumpstarter.driver.proto_marshal import build_marshaller, decode_request, encode_response


def _spec(driver, export_name):
    marshaller = build_marshaller(driver)
    assert marshaller is not None, "driver should have a native surface"
    for spec in marshaller.methods.values():
        if spec.export_name == export_name:
            return spec
    raise AssertionError(f"no spec for {export_name!r} (have {[s.export_name for s in marshaller.methods.values()]})")


# --- drivers under test -----------------------------------------------------------------


class ScalarDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.ScalarClient"

    @export
    async def echo(self, s: str, i: int, f: float, b: bool) -> str: ...


class BytesDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.BytesClient"

    @export
    async def blob(self, data: bytes) -> bytes: ...


class UuidDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.UuidClient"

    @export
    async def ident(self, u: UUID) -> UUID: ...


class DictDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.DictClient"

    @export
    async def meta(self, d: dict) -> dict: ...


class OptionalDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.OptionalClient"

    @export
    async def maybe(self, x: int | None = None) -> None: ...


class ListDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.ListClient"

    @export
    async def nums(self, xs: list[int]) -> list[int]: ...

    @export
    async def uniq(self, xs: set[int]) -> None: ...


class Reading(BaseModel):
    voltage: float
    current: float


class ModelDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.ModelClient"

    @export
    async def read_one(self) -> Reading: ...


@dataclasses.dataclass
class Point:
    x: int
    y: int


class DataclassDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.DataclassClient"

    @export
    async def where(self) -> Point: ...


class NoneDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.NoneClient"

    @export
    async def poke(self) -> None: ...


class MultiWordDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.MultiWordClient"

    @export
    async def get_cpu_info(self) -> str: ...


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"


class EnumDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.EnumClient"

    @export
    async def pick(self, c: Color) -> None: ...


# --- decode (wire → native) -------------------------------------------------------------


def test_scalars_decode_positional():
    spec = _spec(ScalarDriver(), "echo")
    req = spec.input_cls(s="hi", i=7, f=1.5, b=True)
    assert decode_request(spec, req.SerializeToString()) == ["hi", 7, 1.5, True]


def test_bytes_stay_raw():
    spec = _spec(BytesDriver(), "blob")
    raw = b"\xff\x00\x80\x01"  # deliberately not valid UTF-8
    assert decode_request(spec, spec.input_cls(data=raw).SerializeToString()) == [raw]
    # and raw on the way back out (wrapped scalar return)
    out = spec.output_cls.FromString(encode_response(spec, raw))
    assert out.value == raw


def test_uuid_reconciled_from_string():
    spec = _spec(UuidDriver(), "ident")
    u = uuid4()
    assert decode_request(spec, spec.input_cls(u=str(u)).SerializeToString()) == [u]
    out = spec.output_cls.FromString(encode_response(spec, u))
    assert out.value == str(u)


def test_dict_via_value():
    spec = _spec(DictDriver(), "meta")
    req = spec.input_cls()
    ParseDict({"a": 1, "b": [True, "x"], "c": {"n": 2.5}}, req.d)
    (decoded,) = decode_request(spec, req.SerializeToString())
    assert decoded == {"a": 1.0, "b": [True, "x"], "c": {"n": 2.5}}  # Struct coerces ints to float


def test_optional_unset_is_none():
    spec = _spec(OptionalDriver(), "maybe")
    assert decode_request(spec, spec.input_cls().SerializeToString()) == [None]


def test_optional_set_is_value():
    spec = _spec(OptionalDriver(), "maybe")
    assert decode_request(spec, spec.input_cls(x=7).SerializeToString()) == [7]


def test_list_repeated():
    spec = _spec(ListDriver(), "nums")
    assert decode_request(spec, spec.input_cls(xs=[1, 2, 3]).SerializeToString()) == [[1, 2, 3]]
    assert list(spec.output_cls.FromString(encode_response(spec, [4, 5])).value) == [4, 5]


def test_set_repeated_compares_set_equal():
    spec = _spec(ListDriver(), "uniq")
    (decoded,) = decode_request(spec, spec.input_cls(xs=[2, 1, 2]).SerializeToString())
    assert decoded == {1, 2}
    assert isinstance(decoded, set)


# --- encode (native → wire) -------------------------------------------------------------


def test_scalar_return_is_wrapped():
    spec = _spec(ScalarDriver(), "echo")
    assert spec.output_kind == "wrapper"
    assert spec.output_cls.FromString(encode_response(spec, "hello")).value == "hello"


def test_basemodel_return_is_bare_message():
    spec = _spec(ModelDriver(), "read_one")
    assert spec.output_kind == "bare"
    assert [f.name for f in spec.output_desc.fields] == ["voltage", "current"]  # NOT a `value` wrapper
    out = spec.output_cls.FromString(encode_response(spec, Reading(voltage=5.0, current=2.0)))
    assert (out.voltage, out.current) == (5.0, 2.0)


def test_dataclass_return_is_bare_message():
    spec = _spec(DataclassDriver(), "where")
    assert spec.output_kind == "bare"
    out = spec.output_cls.FromString(encode_response(spec, Point(1, 2)))
    assert (out.x, out.y) == (1, 2)


def test_none_return_is_empty():
    spec = _spec(NoneDriver(), "poke")
    assert spec.output_kind == "empty"
    assert encode_response(spec, None) == b""


# --- dispatch metadata & safety valve ---------------------------------------------------


def test_path_fidelity_multiword():
    spec = _spec(MultiWordDriver(), "get_cpu_info")
    assert spec.grpc_path.endswith("/GetCpuInfo")


def test_unknown_path_absent_from_table():
    marshaller = build_marshaller(NoneDriver())
    assert "/does.not/Exist" not in marshaller.methods


def test_uninspectable_driver_returns_none(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("cannot introspect")

    monkeypatch.setattr("jumpstarter.driver.proto_marshal.build_file_descriptor_set", _boom)
    assert build_marshaller(NoneDriver()) is None  # → host raises Unimplemented → Rust JSON fallback


def test_enum_param_not_yet_native_falls_back():
    # Generated enums are declared nested but referenced top-level (a descriptor_builder limitation),
    # so an enum-param interface is not poolable and gracefully degrades to the JSON path. Documents
    # the current boundary of native coverage (tracked as a descriptor_builder follow-up).
    assert build_marshaller(EnumDriver()) is None


def test_malformed_request_bytes_raise():
    spec = _spec(BytesDriver(), "blob")
    with pytest.raises(Exception):  # noqa: B017,PT011 — protobuf DecodeError surfaces to the host as a typed error
        decode_request(spec, b"\x0a\x05ab")  # length-delimited field claims 5 bytes, supplies 2

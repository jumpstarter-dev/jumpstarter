"""Tests for type mapping module (Phase 3): Python types → protobuf descriptors."""

import dataclasses
import enum
from typing import Any, Literal, Optional
from uuid import UUID

import pytest
from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
)
from pydantic import BaseModel

from .type_mapping import (
    EMPTY_TYPE,
    VALUE_TYPE,
    TypeMappingResult,
    map_python_type,
)

PKG = "test.package.v1"


# ---------------------------------------------------------------------------
# Primitive type mapping (parameterized)
# ---------------------------------------------------------------------------

class TestPrimitiveTypes:
    """Verify each primitive Python type maps to the correct protobuf type."""

    @pytest.mark.parametrize(
        "python_type, expected_proto_type",
        [
            (str, FieldDescriptorProto.TYPE_STRING),
            (int, FieldDescriptorProto.TYPE_INT64),
            (float, FieldDescriptorProto.TYPE_DOUBLE),
            (bool, FieldDescriptorProto.TYPE_BOOL),
            (bytes, FieldDescriptorProto.TYPE_BYTES),
        ],
        ids=["str", "int", "float", "bool", "bytes"],
    )
    def test_primitive_mapping(self, python_type, expected_proto_type):
        result = map_python_type(python_type, PKG)
        assert result.field_type == expected_proto_type
        assert result.type_name is None
        assert result.messages == []
        assert result.enums == []

    def test_uuid_maps_to_string(self):
        result = map_python_type(UUID, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_STRING
        assert result.type_name is None


# ---------------------------------------------------------------------------
# None / Empty
# ---------------------------------------------------------------------------

class TestNoneType:
    """None and NoneType should map to google.protobuf.Empty."""

    def test_none_literal(self):
        result = map_python_type(None, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == EMPTY_TYPE

    def test_none_type(self):
        result = map_python_type(type(None), PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == EMPTY_TYPE


# ---------------------------------------------------------------------------
# dict / Any → Value
# ---------------------------------------------------------------------------

class TestDynamicTypes:
    """dict and Any should map to google.protobuf.Value."""

    def test_dict_maps_to_value(self):
        result = map_python_type(dict, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == VALUE_TYPE

    def test_any_maps_to_value(self):
        result = map_python_type(Any, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == VALUE_TYPE

    def test_parameterized_dict_maps_to_value(self):
        result = map_python_type(dict[str, int], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == VALUE_TYPE


# ---------------------------------------------------------------------------
# Optional[T]
# ---------------------------------------------------------------------------

class TestOptionalType:
    """Optional[T] should produce an optional field of inner type T."""

    def test_optional_str(self):
        result = map_python_type(Optional[str], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_STRING
        assert result.is_optional is True

    def test_optional_int(self):
        result = map_python_type(Optional[int], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_INT64
        assert result.is_optional is True

    def test_optional_preserves_inner_type(self):
        result = map_python_type(Optional[float], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_DOUBLE
        assert result.is_optional is True


# ---------------------------------------------------------------------------
# list[T] / set[T] → repeated
# ---------------------------------------------------------------------------

class TestRepeatedTypes:
    """list[T] and set[T] should produce repeated fields."""

    def test_list_int(self):
        result = map_python_type(list[int], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_INT64
        assert result.label == FieldDescriptorProto.LABEL_REPEATED

    def test_list_str(self):
        result = map_python_type(list[str], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_STRING
        assert result.label == FieldDescriptorProto.LABEL_REPEATED

    def test_set_float(self):
        result = map_python_type(set[float], PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_DOUBLE
        assert result.label == FieldDescriptorProto.LABEL_REPEATED

    def test_unparameterized_list(self):
        """list without type param → repeated Value."""
        result = map_python_type(list, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == VALUE_TYPE
        assert result.label == FieldDescriptorProto.LABEL_REPEATED


# ---------------------------------------------------------------------------
# Enum types
# ---------------------------------------------------------------------------

class TestEnumTypes:
    """Python Enum and Literal should produce proto enum descriptors."""

    def test_str_enum(self):
        class Color(enum.StrEnum):
            RED = "red"
            GREEN = "green"
            BLUE = "blue"

        result = map_python_type(Color, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_ENUM
        assert result.type_name == f".{PKG}.Color"
        assert len(result.enums) == 1

        enum_desc = result.enums[0]
        assert enum_desc.name == "Color"
        # Proto3 requires UNSPECIFIED=0 first
        assert enum_desc.value[0].name == "COLOR_UNSPECIFIED"
        assert enum_desc.value[0].number == 0
        # Then the actual values
        assert len(enum_desc.value) == 4  # UNSPECIFIED + 3 values
        value_names = [v.name for v in enum_desc.value[1:]]
        assert "COLOR_RED" in value_names
        assert "COLOR_GREEN" in value_names
        assert "COLOR_BLUE" in value_names

    def test_int_enum(self):
        class Priority(enum.IntEnum):
            LOW = 1
            MEDIUM = 2
            HIGH = 3

        result = map_python_type(Priority, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_ENUM
        assert len(result.enums) == 1
        assert result.enums[0].name == "Priority"

    def test_literal_strings(self):
        result = map_python_type(
            Literal["on", "off", "standby"], PKG, field_name="power_state"
        )
        assert result.field_type == FieldDescriptorProto.TYPE_ENUM
        assert result.type_name == f".{PKG}.PowerStateEnum"
        assert len(result.enums) == 1

        enum_desc = result.enums[0]
        assert enum_desc.value[0].name == "POWERSTATEENUM_UNSPECIFIED"
        assert enum_desc.value[0].number == 0
        assert len(enum_desc.value) == 4  # UNSPECIFIED + 3 values


# ---------------------------------------------------------------------------
# Pydantic BaseModel → DescriptorProto
# ---------------------------------------------------------------------------

class TestBaseModel:
    """Pydantic BaseModel should generate a DescriptorProto message."""

    def test_simple_model(self):
        class SensorReading(BaseModel):
            voltage: float
            current: float

        result = map_python_type(SensorReading, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == f".{PKG}.SensorReading"
        assert len(result.messages) == 1

        msg = result.messages[0]
        assert msg.name == "SensorReading"
        assert len(msg.field) == 2
        assert msg.field[0].name == "voltage"
        assert msg.field[0].type == FieldDescriptorProto.TYPE_DOUBLE
        assert msg.field[0].number == 1
        assert msg.field[1].name == "current"
        assert msg.field[1].type == FieldDescriptorProto.TYPE_DOUBLE
        assert msg.field[1].number == 2

    def test_model_with_optional_field(self):
        class Config(BaseModel):
            name: str
            description: Optional[str] = None

        result = map_python_type(Config, PKG)
        msg = result.messages[0]
        assert msg.field[1].name == "description"
        assert msg.field[1].proto3_optional is True

    def test_model_with_list_field(self):
        class Response(BaseModel):
            values: list[int]

        result = map_python_type(Response, PKG)
        msg = result.messages[0]
        assert msg.field[0].name == "values"
        assert msg.field[0].label == FieldDescriptorProto.LABEL_REPEATED
        assert msg.field[0].type == FieldDescriptorProto.TYPE_INT64


# ---------------------------------------------------------------------------
# Dataclass → DescriptorProto
# ---------------------------------------------------------------------------

class TestDataclass:
    """@dataclass should generate a DescriptorProto message."""

    def test_simple_dataclass(self):
        @dataclasses.dataclass
        class Point:
            x: float
            y: float

        result = map_python_type(Point, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == f".{PKG}.Point"
        assert len(result.messages) == 1

        msg = result.messages[0]
        assert msg.name == "Point"
        assert len(msg.field) == 2
        assert msg.field[0].name == "x"
        assert msg.field[0].number == 1
        assert msg.field[1].name == "y"
        assert msg.field[1].number == 2

    def test_dataclass_with_mixed_types(self):
        @dataclasses.dataclass
        class DeviceInfo:
            name: str
            port: int
            active: bool

        result = map_python_type(DeviceInfo, PKG)
        msg = result.messages[0]
        assert msg.field[0].type == FieldDescriptorProto.TYPE_STRING
        assert msg.field[1].type == FieldDescriptorProto.TYPE_INT64
        assert msg.field[2].type == FieldDescriptorProto.TYPE_BOOL


# ---------------------------------------------------------------------------
# TypeMappingResult
# ---------------------------------------------------------------------------

class TestTypeMappingResult:
    """Verify TypeMappingResult defaults and construction."""

    def test_defaults(self):
        result = TypeMappingResult(field_type=FieldDescriptorProto.TYPE_STRING)
        assert result.type_name is None
        assert result.label == FieldDescriptorProto.LABEL_OPTIONAL
        assert result.is_optional is False
        assert result.messages == []
        assert result.enums == []

    def test_with_all_fields(self):
        msg = DescriptorProto(name="Test")
        enum_desc = EnumDescriptorProto(name="TestEnum")
        result = TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=".pkg.Test",
            label=FieldDescriptorProto.LABEL_REPEATED,
            is_optional=True,
            messages=[msg],
            enums=[enum_desc],
        )
        assert result.type_name == ".pkg.Test"
        assert result.label == FieldDescriptorProto.LABEL_REPEATED
        assert result.is_optional is True
        assert len(result.messages) == 1
        assert len(result.enums) == 1


# ---------------------------------------------------------------------------
# Edge cases / fallback
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and fallback behavior."""

    def test_unknown_type_falls_back_to_value(self):
        """Types that can't be mapped should fall back to Value."""

        class UnknownCustomType:
            pass

        result = map_python_type(UnknownCustomType, PKG)
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE
        assert result.type_name == VALUE_TYPE

    def test_list_of_models(self):
        """list[BaseModel] should produce repeated message."""

        class Item(BaseModel):
            name: str

        result = map_python_type(list[Item], PKG)
        assert result.label == FieldDescriptorProto.LABEL_REPEATED
        assert result.field_type == FieldDescriptorProto.TYPE_MESSAGE

    def test_field_name_used_for_pascal_case(self):
        """field_name should influence generated type names."""
        result = map_python_type(
            Literal["a", "b"], PKG, field_name="my_status"
        )
        assert "MyStatus" in result.type_name

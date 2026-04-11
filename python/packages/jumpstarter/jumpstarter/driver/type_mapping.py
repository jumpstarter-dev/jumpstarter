"""
Map Python type annotations to protobuf field/message/enum descriptors.

Uses Pydantic's TypeAdapter for JSON Schema generation as an intermediate
representation, then maps JSON Schema types to protobuf descriptor types.
This module is consumed by build_file_descriptor() in Phase 4.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    EnumValueDescriptorProto,
    FieldDescriptorProto,
)
from pydantic import BaseModel, TypeAdapter

# Well-known type names (fully qualified)
EMPTY_TYPE = ".google.protobuf.Empty"
VALUE_TYPE = ".google.protobuf.Value"


class TypeMappingResult:
    """Result of mapping a Python type to protobuf descriptors.

    Attributes:
        field_type: The FieldDescriptorProto.Type value (e.g., TYPE_STRING)
        type_name: Fully-qualified name for MESSAGE/ENUM types (e.g., ".pkg.MyMessage")
        label: LABEL_OPTIONAL or LABEL_REPEATED
        is_optional: Whether this is a proto3 optional field
        messages: Any DescriptorProto messages generated for this type
        enums: Any EnumDescriptorProto enums generated for this type
    """

    __slots__ = ("field_type", "type_name", "label", "is_optional", "messages", "enums")

    def __init__(
        self,
        field_type: int,
        type_name: str | None = None,
        label: int = FieldDescriptorProto.LABEL_OPTIONAL,
        is_optional: bool = False,
        messages: list[DescriptorProto] | None = None,
        enums: list[EnumDescriptorProto] | None = None,
    ):
        self.field_type = field_type
        self.type_name = type_name
        self.label = label
        self.is_optional = is_optional
        self.messages = messages or []
        self.enums = enums or []


# Direct Python type → protobuf type mapping for primitives
_PRIMITIVE_MAP: dict[type, int] = {
    str: FieldDescriptorProto.TYPE_STRING,
    int: FieldDescriptorProto.TYPE_INT64,
    float: FieldDescriptorProto.TYPE_DOUBLE,
    bool: FieldDescriptorProto.TYPE_BOOL,
    bytes: FieldDescriptorProto.TYPE_BYTES,
}


def map_python_type(
    python_type: Any,
    parent_package: str,
    field_name: str = "",
) -> TypeMappingResult:
    """Map a Python type annotation to protobuf descriptor components.

    Args:
        python_type: The Python type annotation to map.
        parent_package: The protobuf package for generated messages/enums.
        field_name: Name hint for generated message/enum types.

    Returns:
        TypeMappingResult with the protobuf type information and any
        generated message/enum descriptors.
    """
    # Handle None type (used for return types)
    if python_type is None or python_type is type(None):
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=EMPTY_TYPE,
        )

    # Handle primitive types
    if python_type in _PRIMITIVE_MAP:
        return TypeMappingResult(field_type=_PRIMITIVE_MAP[python_type])

    # Handle UUID → string
    if python_type is UUID:
        return TypeMappingResult(field_type=FieldDescriptorProto.TYPE_STRING)

    # Handle dict / Any → google.protobuf.Value
    if python_type is dict or python_type is Any:
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=VALUE_TYPE,
        )

    origin = get_origin(python_type)
    args = get_args(python_type)

    # Handle Optional[T] (Union[T, None])
    if origin is Union:
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1 and len(args) == 2:
            # This is Optional[T]
            inner = map_python_type(non_none_args[0], parent_package, field_name)
            inner.is_optional = True
            return inner
        # General Union → fallback to Value
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=VALUE_TYPE,
        )

    # Handle list[T] / set[T] → repeated
    if origin is list or origin is set:
        if args:
            inner = map_python_type(args[0], parent_package, field_name)
            inner.label = FieldDescriptorProto.LABEL_REPEATED
            return inner
        # Unparameterized list → repeated Value
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=VALUE_TYPE,
            label=FieldDescriptorProto.LABEL_REPEATED,
        )

    # Handle Literal["a", "b"] → proto enum
    if origin is Literal:
        enum_name = _to_pascal_case(field_name) + "Enum" if field_name else "LiteralEnum"
        enum_desc = _build_literal_enum(enum_name, args)
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_ENUM,
            type_name=f".{parent_package}.{enum_name}",
            enums=[enum_desc],
        )

    # Handle enum.Enum / StrEnum → proto enum
    if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
        enum_name = python_type.__name__
        enum_desc = _build_python_enum(enum_name, python_type)
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_ENUM,
            type_name=f".{parent_package}.{enum_name}",
            enums=[enum_desc],
        )

    # Handle Pydantic BaseModel → generated DescriptorProto
    if isinstance(python_type, type) and issubclass(python_type, BaseModel):
        msg = _build_basemodel_message(python_type, parent_package)
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=f".{parent_package}.{msg.name}",
            messages=[msg],
        )

    # Handle dataclass → generated DescriptorProto
    if dataclasses.is_dataclass(python_type) and isinstance(python_type, type):
        msg = _build_dataclass_message(python_type, parent_package)
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=f".{parent_package}.{msg.name}",
            messages=[msg],
        )

    # Handle dict with type params (dict[str, X]) → Value fallback
    if origin is dict:
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=VALUE_TYPE,
        )

    # Fallback: use Pydantic TypeAdapter to get JSON Schema
    try:
        schema = TypeAdapter(python_type).json_schema()
        return _map_json_schema(schema, parent_package, field_name)
    except Exception:
        # Ultimate fallback → Value
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=VALUE_TYPE,
        )


def _to_pascal_case(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def _build_literal_enum(
    enum_name: str, values: tuple[Any, ...]
) -> EnumDescriptorProto:
    """Build a proto enum from Literal string values."""
    enum_desc = EnumDescriptorProto(name=enum_name)
    # Proto3 requires first value to be 0
    enum_desc.value.append(
        EnumValueDescriptorProto(
            name=f"{enum_name.upper()}_UNSPECIFIED", number=0
        )
    )
    for i, val in enumerate(values, start=1):
        safe_name = str(val).upper().replace("-", "_").replace(" ", "_")
        enum_desc.value.append(
            EnumValueDescriptorProto(name=f"{enum_name.upper()}_{safe_name}", number=i)
        )
    return enum_desc


def _build_python_enum(
    enum_name: str, enum_type: type[enum.Enum]
) -> EnumDescriptorProto:
    """Build a proto enum from a Python Enum class."""
    enum_desc = EnumDescriptorProto(name=enum_name)
    # Proto3 requires first value to be 0
    enum_desc.value.append(
        EnumValueDescriptorProto(
            name=f"{enum_name.upper()}_UNSPECIFIED", number=0
        )
    )
    for i, member in enumerate(enum_type, start=1):
        enum_desc.value.append(
            EnumValueDescriptorProto(
                name=f"{enum_name.upper()}_{member.name.upper()}", number=i
            )
        )
    return enum_desc


def _build_basemodel_message(
    model: type[BaseModel], parent_package: str
) -> DescriptorProto:
    """Build a DescriptorProto from a Pydantic BaseModel."""
    msg = DescriptorProto(name=model.__name__)
    nested_messages = []
    nested_enums = []

    for i, (field_name, field_info) in enumerate(model.model_fields.items(), start=1):
        result = map_python_type(field_info.annotation, parent_package, field_name)

        field = FieldDescriptorProto(
            name=field_name,
            number=i,
            type=result.field_type,
            label=result.label,
        )
        if result.type_name:
            field.type_name = result.type_name
        if result.is_optional:
            field.proto3_optional = True

        msg.field.append(field)
        nested_messages.extend(result.messages)
        nested_enums.extend(result.enums)

    for nested in nested_messages:
        msg.nested_type.append(nested)
    for nested_enum in nested_enums:
        msg.enum_type.append(nested_enum)

    return msg


def _build_dataclass_message(
    dc: type, parent_package: str
) -> DescriptorProto:
    """Build a DescriptorProto from a dataclass."""
    msg = DescriptorProto(name=dc.__name__)
    nested_messages = []
    nested_enums = []

    for i, field in enumerate(dataclasses.fields(dc), start=1):
        result = map_python_type(field.type, parent_package, field.name)

        proto_field = FieldDescriptorProto(
            name=field.name,
            number=i,
            type=result.field_type,
            label=result.label,
        )
        if result.type_name:
            proto_field.type_name = result.type_name
        if result.is_optional:
            proto_field.proto3_optional = True

        msg.field.append(proto_field)
        nested_messages.extend(result.messages)
        nested_enums.extend(result.enums)

    for nested in nested_messages:
        msg.nested_type.append(nested)
    for nested_enum in nested_enums:
        msg.enum_type.append(nested_enum)

    return msg


# JSON Schema type → protobuf type
_JSON_SCHEMA_TYPE_MAP: dict[str, int] = {
    "integer": FieldDescriptorProto.TYPE_INT64,
    "number": FieldDescriptorProto.TYPE_DOUBLE,
    "string": FieldDescriptorProto.TYPE_STRING,
    "boolean": FieldDescriptorProto.TYPE_BOOL,
}


def _map_json_schema(
    schema: dict[str, Any], parent_package: str, field_name: str
) -> TypeMappingResult:
    """Map a JSON Schema dict to protobuf type information."""
    schema_type = schema.get("type")

    if schema_type in _JSON_SCHEMA_TYPE_MAP:
        return TypeMappingResult(field_type=_JSON_SCHEMA_TYPE_MAP[schema_type])

    if schema_type == "array":
        items = schema.get("items", {})
        inner = _map_json_schema(items, parent_package, field_name)
        inner.label = FieldDescriptorProto.LABEL_REPEATED
        return inner

    if schema_type == "object":
        # Generate a message
        msg_name = _to_pascal_case(field_name) if field_name else "Object"
        msg = DescriptorProto(name=msg_name)
        properties = schema.get("properties", {})
        for i, (prop_name, prop_schema) in enumerate(properties.items(), start=1):
            inner = _map_json_schema(prop_schema, parent_package, prop_name)
            field = FieldDescriptorProto(
                name=prop_name,
                number=i,
                type=inner.field_type,
                label=inner.label,
            )
            if inner.type_name:
                field.type_name = inner.type_name
            msg.field.append(field)
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_MESSAGE,
            type_name=f".{parent_package}.{msg_name}",
            messages=[msg],
        )

    # anyOf with null → optional
    any_of = schema.get("anyOf")
    if any_of:
        non_null = [s for s in any_of if s.get("type") != "null"]
        if len(non_null) == 1:
            inner = _map_json_schema(non_null[0], parent_package, field_name)
            inner.is_optional = True
            return inner

    # enum values
    if "enum" in schema:
        enum_name = _to_pascal_case(field_name) + "Enum" if field_name else "Enum"
        enum_desc = _build_literal_enum(enum_name, tuple(schema["enum"]))
        return TypeMappingResult(
            field_type=FieldDescriptorProto.TYPE_ENUM,
            type_name=f".{parent_package}.{enum_name}",
            enums=[enum_desc],
        )

    # Fallback
    return TypeMappingResult(
        field_type=FieldDescriptorProto.TYPE_MESSAGE,
        type_name=VALUE_TYPE,
    )

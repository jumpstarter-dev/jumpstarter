from typing import Any

from google.protobuf import json_format, struct_pb2
from pydantic import TypeAdapter

adapter = TypeAdapter(Any)


def encode_value(v: Any):
    return json_format.ParseDict(adapter.dump_python(v, mode="json"), struct_pb2.Value())


def decode_value(v: struct_pb2.Value) -> Any:
    return adapter.validate_python(json_format.MessageToDict(v))

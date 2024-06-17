from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ServerCommand(_message.Message):
    __slots__ = ("ping", "driver_call", "event")
    PING_FIELD_NUMBER: _ClassVar[int]
    DRIVER_CALL_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    ping: Ping
    driver_call: DriverCall
    event: Event
    def __init__(self, ping: _Optional[_Union[Ping, _Mapping]] = ..., driver_call: _Optional[_Union[DriverCall, _Mapping]] = ..., event: _Optional[_Union[Event, _Mapping]] = ...) -> None: ...

class Ping(_message.Message):
    __slots__ = ("data", "seq")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    data: str
    seq: int
    def __init__(self, data: _Optional[str] = ..., seq: _Optional[int] = ...) -> None: ...

class DriverCall(_message.Message):
    __slots__ = ("device_id", "call_uuid", "driver_method", "args")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    CALL_UUID_FIELD_NUMBER: _ClassVar[int]
    DRIVER_METHOD_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    call_uuid: str
    driver_method: str
    args: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Value]
    def __init__(self, device_id: _Optional[str] = ..., call_uuid: _Optional[str] = ..., driver_method: _Optional[str] = ..., args: _Optional[_Iterable[_Union[_struct_pb2.Value, _Mapping]]] = ...) -> None: ...

class ExporterBye(_message.Message):
    __slots__ = ("uuid", "reason")
    UUID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    uuid: str
    reason: str
    def __init__(self, uuid: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class ExporterReport(_message.Message):
    __slots__ = ("uuid", "hostname", "name", "labels", "device_report")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    UUID_FIELD_NUMBER: _ClassVar[int]
    HOSTNAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    DEVICE_REPORT_FIELD_NUMBER: _ClassVar[int]
    uuid: str
    hostname: str
    name: str
    labels: _containers.ScalarMap[str, str]
    device_report: _containers.RepeatedCompositeFieldContainer[DeviceReport]
    def __init__(self, uuid: _Optional[str] = ..., hostname: _Optional[str] = ..., name: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., device_report: _Optional[_Iterable[_Union[DeviceReport, _Mapping]]] = ...) -> None: ...

class DeviceReport(_message.Message):
    __slots__ = ("device_id", "parent_device_id", "device_name", "driver_interface")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    DEVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    DRIVER_INTERFACE_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    parent_device_id: str
    device_name: str
    driver_interface: str
    def __init__(self, device_id: _Optional[str] = ..., parent_device_id: _Optional[str] = ..., device_name: _Optional[str] = ..., driver_interface: _Optional[str] = ...) -> None: ...

class ClientResponse(_message.Message):
    __slots__ = ("pong", "exporter_report", "driver_response", "event", "log")
    PONG_FIELD_NUMBER: _ClassVar[int]
    EXPORTER_REPORT_FIELD_NUMBER: _ClassVar[int]
    DRIVER_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    LOG_FIELD_NUMBER: _ClassVar[int]
    pong: Pong
    exporter_report: ExporterReport
    driver_response: DriverResponse
    event: Event
    log: Log
    def __init__(self, pong: _Optional[_Union[Pong, _Mapping]] = ..., exporter_report: _Optional[_Union[ExporterReport, _Mapping]] = ..., driver_response: _Optional[_Union[DriverResponse, _Mapping]] = ..., event: _Optional[_Union[Event, _Mapping]] = ..., log: _Optional[_Union[Log, _Mapping]] = ...) -> None: ...

class Pong(_message.Message):
    __slots__ = ("data", "seq")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    data: str
    seq: int
    def __init__(self, data: _Optional[str] = ..., seq: _Optional[int] = ...) -> None: ...

class DriverResponse(_message.Message):
    __slots__ = ("call_uuid", "json_result")
    CALL_UUID_FIELD_NUMBER: _ClassVar[int]
    JSON_RESULT_FIELD_NUMBER: _ClassVar[int]
    call_uuid: str
    json_result: str
    def __init__(self, call_uuid: _Optional[str] = ..., json_result: _Optional[str] = ...) -> None: ...

class Event(_message.Message):
    __slots__ = ("device_id", "handle", "type", "data")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    handle: str
    type: str
    data: str
    def __init__(self, device_id: _Optional[str] = ..., handle: _Optional[str] = ..., type: _Optional[str] = ..., data: _Optional[str] = ...) -> None: ...

class Log(_message.Message):
    __slots__ = ("device_id", "handle", "level", "message")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    handle: str
    level: str
    message: str
    def __init__(self, device_id: _Optional[str] = ..., handle: _Optional[str] = ..., level: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class DataChunk(_message.Message):
    __slots__ = ("device_id", "handle", "seq", "offset", "data")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLE_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    handle: str
    seq: int
    offset: int
    data: bytes
    def __init__(self, device_id: _Optional[str] = ..., handle: _Optional[str] = ..., seq: _Optional[int] = ..., offset: _Optional[int] = ..., data: _Optional[bytes] = ...) -> None: ...

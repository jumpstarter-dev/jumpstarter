from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ServerCommand(_message.Message):
    __slots__ = ("ping", "driver_call")
    PING_FIELD_NUMBER: _ClassVar[int]
    DRIVER_CALL_FIELD_NUMBER: _ClassVar[int]
    ping: Ping
    driver_call: DriverCall
    def __init__(self, ping: _Optional[_Union[Ping, _Mapping]] = ..., driver_call: _Optional[_Union[DriverCall, _Mapping]] = ...) -> None: ...

class Ping(_message.Message):
    __slots__ = ("data", "seq")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    data: str
    seq: int
    def __init__(self, data: _Optional[str] = ..., seq: _Optional[int] = ...) -> None: ...

class DriverCall(_message.Message):
    __slots__ = ("device_id", "driver_method", "argument_json")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    DRIVER_METHOD_FIELD_NUMBER: _ClassVar[int]
    ARGUMENT_JSON_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    driver_method: str
    argument_json: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, device_id: _Optional[str] = ..., driver_method: _Optional[str] = ..., argument_json: _Optional[_Iterable[str]] = ...) -> None: ...

class ExporterBye(_message.Message):
    __slots__ = ("uuid", "reason")
    UUID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    uuid: str
    reason: str
    def __init__(self, uuid: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class ExporterReport(_message.Message):
    __slots__ = ("uuid", "hostname", "name", "labels", "driver_report")
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
    DRIVER_REPORT_FIELD_NUMBER: _ClassVar[int]
    uuid: str
    hostname: str
    name: str
    labels: _containers.ScalarMap[str, str]
    driver_report: _containers.RepeatedCompositeFieldContainer[DriverReport]
    def __init__(self, uuid: _Optional[str] = ..., hostname: _Optional[str] = ..., name: _Optional[str] = ..., labels: _Optional[_Mapping[str, str]] = ..., driver_report: _Optional[_Iterable[_Union[DriverReport, _Mapping]]] = ...) -> None: ...

class DriverReport(_message.Message):
    __slots__ = ("device_id", "parent_device_id", "driver_name", "driver_interface")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    DRIVER_NAME_FIELD_NUMBER: _ClassVar[int]
    DRIVER_INTERFACE_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    parent_device_id: str
    driver_name: str
    driver_interface: str
    def __init__(self, device_id: _Optional[str] = ..., parent_device_id: _Optional[str] = ..., driver_name: _Optional[str] = ..., driver_interface: _Optional[str] = ...) -> None: ...

class ClientResponse(_message.Message):
    __slots__ = ("pong", "exporter_report", "driver_response")
    PONG_FIELD_NUMBER: _ClassVar[int]
    EXPORTER_REPORT_FIELD_NUMBER: _ClassVar[int]
    DRIVER_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    pong: Pong
    exporter_report: ExporterReport
    driver_response: DriverResponse
    def __init__(self, pong: _Optional[_Union[Pong, _Mapping]] = ..., exporter_report: _Optional[_Union[ExporterReport, _Mapping]] = ..., driver_response: _Optional[_Union[DriverResponse, _Mapping]] = ...) -> None: ...

class Pong(_message.Message):
    __slots__ = ("data", "seq")
    DATA_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    data: str
    seq: int
    def __init__(self, data: _Optional[str] = ..., seq: _Optional[int] = ...) -> None: ...

class DriverResponse(_message.Message):
    __slots__ = ("json_result",)
    JSON_RESULT_FIELD_NUMBER: _ClassVar[int]
    json_result: str
    def __init__(self, json_result: _Optional[str] = ...) -> None: ...

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

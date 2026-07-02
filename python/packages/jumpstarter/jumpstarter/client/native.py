"""Native (proto-bytes) client runtime — the base class for GENERATED typed clients.

A `jumpstarter-codegen --language python` client subclasses :class:`NativeDriverClient` and sets
two class attributes from its generated ``<stem>_descriptor.py``: ``_DESCRIPTOR_SET`` (the
interface's serialized ``FileDescriptorSet``) and ``_SERVICE_FULL_NAME``. Each generated method is
one `_native_unary`/`_native_server_stream` call: native args are encoded into the request message,
the session's native (proto-bytes) seam carries it — ``ClientSession.native_unary`` over the wire,
or ``LocalSession`` straight into the host's ``forward_unary`` in `serve()` — and the response
decodes back into native values (stdlib dataclasses / scalars). The whole codec is
``jumpstarter.driver.proto_marshal``; nothing here touches JSON or the legacy ``driver_call`` path.
"""

from __future__ import annotations

from typing import Any, ClassVar

import jumpstarter_core

from .base import DriverClient
from .core import _map_ffi_error
from jumpstarter.driver.proto_marshal import (
    ServiceMarshaller,
    build_service_marshaller,
    decode_result,
    encode_args,
)


class NativeDriverClient(DriverClient):
    """Base for generated proto-first clients: typed methods over the native (proto-bytes) seam."""

    # Set by the generated subclass (from its <stem>_descriptor.py).
    _DESCRIPTOR_SET: ClassVar[bytes] = b""
    _SERVICE_FULL_NAME: ClassVar[str] = ""

    @classmethod
    def _marshaller(cls) -> ServiceMarshaller:
        """The service's codec table, built once per generated client class (cached on the class
        itself — ``cls.__dict__`` so subclasses don't inherit a sibling's cache)."""
        cached = cls.__dict__.get("_service_marshaller")
        if cached is None:
            if not cls._DESCRIPTOR_SET or not cls._SERVICE_FULL_NAME:
                raise TypeError(
                    f"{cls.__name__} must set _DESCRIPTOR_SET/_SERVICE_FULL_NAME "
                    "(generated from its interface descriptor)"
                )
            cached = build_service_marshaller(cls._DESCRIPTOR_SET, cls._SERVICE_FULL_NAME)
            cls._service_marshaller = cached
        return cached

    def _native_unary(self, path: str, model: type | None, args: list[Any]) -> Any:
        """Drive one unary RPC: encode ``args`` → request bytes, call the session's native seam,
        decode the response (``model`` = the native message dataclass for a bare-message response,
        ``None`` for Empty / a ``value`` wrapper)."""
        spec = self._marshaller().methods[path]
        body = encode_args(spec, args)
        try:
            resp = self.portal.call(self.session.native_unary, str(self.uuid), path, body)
        except jumpstarter_core.DriverError as e:
            raise _map_ffi_error(path, e) from None
        return decode_result(spec, bytes(resp), model)

    def _native_server_stream(self, path: str, model: type | None, args: list[Any]):
        """Drive one server-streaming RPC, yielding each response message decoded to native."""
        spec = self._marshaller().methods[path]
        body = encode_args(spec, args)
        try:
            stream = self.portal.call(self.session.native_server_stream, str(self.uuid), path, body)
            while True:
                item = self.portal.call(stream.next)
                if item is None:
                    break
                yield decode_result(spec, bytes(item), model)
        except jumpstarter_core.DriverError as e:
            raise _map_ffi_error(path, e) from None

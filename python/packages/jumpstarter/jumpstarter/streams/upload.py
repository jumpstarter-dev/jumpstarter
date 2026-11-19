from dataclasses import dataclass, field

from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream

from jumpstarter.streams.common import create_memory_stream


@dataclass(kw_only=True)
class _UploadSender(ObjectStream[bytes]):
    """The transport's end: ``send_eof()`` marks the upload complete."""

    stream: ObjectStream[bytes]
    complete: bool = field(default=False, init=False)

    async def send(self, item: bytes) -> None:
        await self.stream.send(item)

    async def receive(self) -> bytes:
        return await self.stream.receive()

    async def send_eof(self) -> None:
        self.complete = True
        await self.stream.send_eof()

    async def aclose(self) -> None:
        await self.stream.aclose()


@dataclass(kw_only=True)
class _UploadReceiver(ObjectStream[bytes]):
    """The consumer's end: the upload only ends cleanly if the sender completed it."""

    stream: ObjectStream[bytes]
    sender: _UploadSender

    async def send(self, item: bytes) -> None:
        await self.stream.send(item)

    async def receive(self) -> bytes:
        try:
            return await self.stream.receive()
        except EndOfStream:
            if not self.sender.complete:
                raise BrokenResourceError("upload interrupted before the client finished sending it") from None
            raise

    async def send_eof(self) -> None:
        await self.stream.send_eof()

    async def aclose(self) -> None:
        await self.stream.aclose()


def create_upload_stream():
    """Create a memory stream whose reader can tell a finished upload from a lost one.

    Returns ``(remote, local)`` like :func:`create_memory_stream`. The transport
    feeds ``remote`` and the consumer reads ``local``. The upload is complete only
    once ``remote.send_eof()`` is called. If ``remote`` is closed without it, as
    when the transport fails, ``local`` raises ``BrokenResourceError`` after the
    data already received instead of ``EndOfStream``, so the consumer never
    mistakes a truncated upload for a whole one.
    """
    remote, local = create_memory_stream()
    sender = _UploadSender(stream=remote)
    return sender, _UploadReceiver(stream=local, sender=sender)

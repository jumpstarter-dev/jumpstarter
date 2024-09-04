from dataclasses import dataclass

from anyio.abc import AnyByteStream
from anyio.from_thread import BlockingPortal


@dataclass(kw_only=True)
class BlockingStream:
    stream: AnyByteStream
    portal: BlockingPortal

    def send(self, data: bytes) -> None:
        return self.portal.call(self.stream.send, data)

    def receive(self) -> bytes:
        return self.portal.call(self.stream.receive)

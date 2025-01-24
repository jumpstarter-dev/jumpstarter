from dataclasses import dataclass, field

from anyio.abc import ObjectStream
from tqdm import tqdm

TQDM_KWARGS = {
    "unit": "B",
    "unit_scale": True,
    "unit_divisor": 1024,
}


@dataclass(kw_only=True)
class ProgressStream(ObjectStream[bytes]):
    stream: ObjectStream

    __recv: tqdm = field(init=False, default=None)
    __send: tqdm = field(init=False, default=None)

    def __del__(self):
        if self.__recv is not None:
            self.__recv.close()
        if self.__send is not None:
            self.__send.close()

    async def receive(self):
        item = await self.stream.receive()

        if self.__recv is None:
            self.__recv = tqdm(desc="read", **TQDM_KWARGS)
        self.__recv.update(len(item))

        return item

    async def send(self, item):
        if self.__send is None:
            self.__send = tqdm(desc="write", **TQDM_KWARGS)
        self.__send.update(len(item))

        await self.stream.send(item)

    async def send_eof(self):
        await self.stream.send_eof()

    async def aclose(self):
        await self.stream.aclose()

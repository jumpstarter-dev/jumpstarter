import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO

from anyio.abc import ObjectStream
from rich.console import Console
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ProgressStream(ObjectStream[bytes]):
    stream: ObjectStream
    logging: bool = False

    __prog: Progress | None = field(init=False, default=None)
    __recv: TaskID | None = field(init=False, default=None)
    __send: TaskID | None = field(init=False, default=None)
    __last: datetime = field(init=False, default_factory=datetime.now)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.__prog = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TransferSpeedColumn(),
            FileSizeColumn(),
            TimeElapsedColumn(),
            disable=self.logging,
        )

    def __del__(self):
        self.__prog.stop()

    async def receive(self):
        if self.__recv is None:
            self.__prog.start()
            self.__recv = self.__prog.add_task("transfer", total=None)

        item = await self.stream.receive()

        self.__prog.advance(self.__recv, len(item))
        if self.logging and (datetime.now() - self.__last > timedelta(seconds=2)):
            self.__last = datetime.now()
            console = Console(file=StringIO())
            console.print(self.__prog.get_renderable())
            logger.info(console.file.getvalue().rstrip())

        return item

    async def send(self, item):
        if self.__send is None:
            self.__prog.start()
            self.__send = self.__prog.add_task("transfer", total=None)

        self.__prog.advance(self.__recv, len(item))
        if self.logging and (datetime.now() - self.__last > timedelta(seconds=2)):
            self.__last = datetime.now()
            console = Console(file=StringIO())
            console.print(self.__prog.get_renderable())
            logger.info(console.file.getvalue().rstrip())

        await self.stream.send(item)

    async def send_eof(self):
        await self.stream.send_eof()

    async def aclose(self):
        await self.stream.aclose()

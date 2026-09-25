import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from anyio import TypedAttributeSet, typed_attribute
from anyio.abc import ObjectStream
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

logger = logging.getLogger(__name__)


def _log_progress(task: Task) -> str:
    completed_mb = task.completed / 1e6
    total_str = f"{task.total / 1e6:.1f} MB" if task.total else "?"
    speed_str = f"{task.speed / 1e6:.1f} MB/s" if task.speed else "?"
    elapsed_str = str(timedelta(seconds=int(task.elapsed or 0)))
    return (
        f"transfer: {task.percentage:.1f}% | {completed_mb:.1f} MB / {total_str}"
        f" | {speed_str} | elapsed {elapsed_str}"
    )


class ProgressAttribute(TypedAttributeSet):
    total: float = typed_attribute()


@dataclass(kw_only=True)
class ProgressStream(ObjectStream[bytes]):
    stream: ObjectStream
    logging: bool = False

    __prog: Progress | None = field(init=False, default=None)
    __recv: TaskID | None = field(init=False, default=None)
    __send: TaskID | None = field(init=False, default=None)
    __last: datetime = field(init=False, default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.__prog = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TransferSpeedColumn(),
            DownloadColumn(),
            TextColumn("Elapsed:"),
            TimeElapsedColumn(),
            TextColumn("Remaining:"),
            TimeRemainingColumn(),
            disable=self.logging or os.environ.get("TERM") == "dumb",
        )

    def __del__(self):
        if self.__prog.live.is_started:
            self.__prog.stop()

    async def receive(self):
        if self.__recv is None:
            self.__prog.start()
            self.__recv = self.__prog.add_task(
                "transfer",
                total=self.stream.extra(ProgressAttribute.total, None),
            )

        item = await self.stream.receive()

        self.__prog.advance(self.__recv, len(item))
        if self.logging and (datetime.now(tz=UTC) - self.__last > timedelta(seconds=2)):
            self.__last = datetime.now(tz=UTC)
            logger.info(_log_progress(self.__prog.tasks[self.__recv]))

        return item

    async def send(self, item):
        if self.__send is None:
            self.__prog.start()
            self.__send = self.__prog.add_task(
                "transfer",
                total=self.stream.extra(ProgressAttribute.total, None),
            )

        self.__prog.advance(self.__send, len(item))
        if self.logging and (datetime.now(tz=UTC) - self.__last > timedelta(seconds=2)):
            self.__last = datetime.now(tz=UTC)
            logger.info(_log_progress(self.__prog.tasks[self.__send]))

        await self.stream.send(item)

    async def send_eof(self):
        await self.stream.send_eof()

    async def aclose(self):
        await self.stream.aclose()

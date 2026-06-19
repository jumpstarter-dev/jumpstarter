import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from anyio import TypedAttributeSet, typed_attribute
from anyio.abc import ObjectStream

logger = logging.getLogger(__name__)


class ProgressAttribute(TypedAttributeSet):
    total: float = typed_attribute()


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


@dataclass(kw_only=True)
class ProgressStream(ObjectStream[bytes]):
    """Wraps a byte stream and logs throttled transfer progress (percent + speed).

    Replaces the former rich live progress bar with periodic INFO logging (stdlib only), so a
    resource flash/upload still reports progress without the rich dependency.
    """

    stream: ObjectStream
    logging: bool = False

    _total: float | None = field(init=False, default=None)
    _transferred: int = field(init=False, default=0)
    _started: datetime | None = field(init=False, default=None)
    _last: datetime = field(init=False, default_factory=datetime.now)
    _disabled: bool = field(init=False, default=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._disabled = os.environ.get("TERM") == "dumb"

    def _advance(self, count: int) -> None:
        if self._disabled:
            return
        if self._started is None:
            self._started = datetime.now()
            self._total = self.stream.extra(ProgressAttribute.total, None)
        self._transferred += count
        now = datetime.now()
        if now - self._last <= timedelta(seconds=2):
            return
        self._last = now
        elapsed = max((now - self._started).total_seconds(), 1e-6)
        speed = _human(self._transferred / elapsed)
        if self._total:
            pct = 100.0 * self._transferred / self._total
            logger.info(
                "transfer: %.0f%% (%s/%s) %s/s", pct, _human(self._transferred), _human(self._total), speed
            )
        else:
            logger.info("transfer: %s %s/s", _human(self._transferred), speed)

    async def receive(self):
        item = await self.stream.receive()
        self._advance(len(item))
        return item

    async def send(self, item):
        self._advance(len(item))
        await self.stream.send(item)

    async def send_eof(self):
        await self.stream.send_eof()

    async def aclose(self):
        await self.stream.aclose()

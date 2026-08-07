import io
from abc import ABCMeta, abstractmethod
from base64 import b64encode
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

from aiohttp import ClientSession, MultipartReader
from anyio import connect_tcp
from PIL import Image

from .common import VideoState
from jumpstarter.driver import Driver, export, exportstream


class VideoInterface(metaclass=ABCMeta):
    """Common contract for video source drivers.

    Implementations expose:
      - ``snapshot``: a single JPEG frame, base64 encoded
      - ``state``: a :class:`VideoState` (or a subclass carrying more detail)
      - ``stream_path``: HTTP path serving an MJPEG stream on the ``connect`` stream
      - ``connect`` (``@exportstream``): a byte stream to an HTTP server that
        serves MJPEG at ``stream_path``
    """

    driver_type = "video"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_video.client.VideoClient"

    @abstractmethod
    async def snapshot(self) -> str: ...

    @abstractmethod
    def stream_path(self) -> str: ...

    @abstractmethod
    async def state(self): ...


@dataclass(kw_only=True)
class HttpVideo(VideoInterface, Driver):
    """Video driver for HTTP/MJPEG camera sources reachable from the exporter.

    Covers cameras that live on the DUT itself, e.g. ESP32 camera firmware
    serving an MJPEG stream over its network interface, as well as generic
    IP cameras.
    """

    url: str
    snapshot_url: str | None = None

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        parsed = urlparse(self.url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme in stream url: {self.url}")
        if parsed.hostname is None:
            raise ValueError(f"missing host in stream url: {self.url}")

    async def _fetch_frame(self) -> bytes:
        async with ClientSession() as session:
            if self.snapshot_url is not None:
                async with session.get(self.snapshot_url) as resp:
                    resp.raise_for_status()
                    return await resp.read()
            # no snapshot endpoint: grab the first frame off the MJPEG stream
            async with session.get(self.url) as resp:
                resp.raise_for_status()
                reader = MultipartReader.from_response(resp)
                part = await reader.next()
                if part is None:
                    raise RuntimeError(f"no frame received from {self.url}")
                return await part.read()  # type: ignore[union-attr]

    @export
    async def snapshot(self) -> str:
        data = await self._fetch_frame()
        self.logger.debug("snapshot: %d bytes", len(data))
        return b64encode(data).decode("ascii")

    @export
    async def state(self) -> VideoState:
        """Report source state, derived from a frame.

        A plain HTTP camera exposes no status endpoint, so being able to fetch
        a frame is what "online" means here, and the frame itself carries the
        resolution. Frame rate is set by the source and is not reported.
        """
        try:
            data = await self._fetch_frame()
        except Exception as e:
            self.logger.debug("state: source unreachable: %s", e)
            return VideoState(online=False)
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:
            return VideoState(online=True)
        return VideoState(online=True, width=width, height=height)

    @export
    def stream_path(self) -> str:
        parsed = urlparse(self.url)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        return path

    @exportstream
    @asynccontextmanager
    async def connect(self):
        parsed = urlparse(self.url)
        assert parsed.hostname is not None  # validated in __post_init__
        if parsed.scheme == "https":
            port = parsed.port or 443
            self.logger.debug("streaming video from %s:%d (tls)", parsed.hostname, port)
            stream = await connect_tcp(parsed.hostname, port, tls=True)
        else:
            port = parsed.port or 80
            self.logger.debug("streaming video from %s:%d", parsed.hostname, port)
            stream = await connect_tcp(parsed.hostname, port)
        async with stream:
            yield stream

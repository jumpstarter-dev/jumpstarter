"""The HMI interface: what a driver exports to be an HMI."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any


class HmiInterface(metaclass=ABCMeta):
    """A remote display with declared input: a picture, and whatever the device takes back.

    An **HMI** (human-machine interface) here is the device's own interface, not an HMI application that runs on it:
    what a person in front of it sees and does. It covers mobile devices and embedded touch screens, and later remote
    desktops (VNC) and KVM devices.

    *The picture* is one video stream in one codec: H.264 (the default), and HEVC or Motion JPEG where the HMI declares
    them in ``hmi_info()["codecs"]``; ``start(codec=...)`` picks one for a lease. H.264 and HEVC are recorded to an MP4,
    MJPEG is not.

    *The input* is what the device **declares** in ``hmi_info()["input"]``, and nothing more: a ``pointer`` (``touch``,
    ``mouse`` or ``pen``, absolute and/or relative, with buttons and a wheel or not, one contact or several), a
    ``keyboard`` (the key codes it takes, as W3C ``KeyboardEvent.code`` names, and whether it types text), named hard
    ``keys`` (``HOME``, ``BACK``..., each taking the presses it declares), ``rotaries`` (knobs turned by detents, pushed
    with a hard key) and a clipboard. The exporter lets nothing else through, and a UI shows only what
    is declared. An HMI that declares no input at all is a valid, **view-only** HMI.

    A driver implements the interface by subclassing ``(HmiInterface, Driver)`` and exporting the methods below, the way
    the power interface is implemented; :class:`~jumpstarter_driver_hmi.driver.HmiDriver` implements all of them on a
    shared hub, so a device driver supplies only a :class:`~jumpstarter_driver_hmi.source.HmiSource`.

    ``video()`` and ``input()`` are streams (see :mod:`jumpstarter_driver_hmi.common` for the wire format); the rest
    are plain calls. The client of every implementation is a :class:`~jumpstarter_driver_hmi.client.HmiClient`.
    """

    driver_type = "hmi"

    @classmethod
    def client(cls) -> str:
        """Import path of the matching client class."""
        return "jumpstarter_driver_hmi.client.HmiClient"

    @abstractmethod
    def video(self) -> AbstractAsyncContextManager[Any]:
        """The picture as framed access units in the stream's codec, the codec line first; joining starts the capture.
        The client may write ``{"resync": true}`` and ``{"lag": seconds}`` lines.
        """

    @abstractmethod
    def input(self) -> AbstractAsyncContextManager[Any]:
        """Pointer, keyboard, hard key, rotary and text events as JSON lines, of what ``hmi_info()`` declares; errors
        come back as ``{"error": ...}`` lines that the exporter never waits on. A view-only HMI refuses the stream.
        """

    @abstractmethod
    def hmi_info(self) -> dict:
        """What the HMI is and takes, for a UI to show only what is there: ``codecs`` (what it produces) and ``codec``
        (in use or chosen), the picture's size, ``input`` (``pointer``, ``keyboard``, ``keys``, ``hard_keys``,
        ``clipboard``: all empty for a view-only HMI), ``quality`` and whether the stream can be recorded.
        """

    @abstractmethod
    def status(self) -> dict:
        """What the stream is doing: its state, picture, viewers and quality, and the device's own facts under
        ``device``.
        """

    @abstractmethod
    def start(
        self,
        quality: str | int | None = None,
        max_size: int | None = None,
        bit_rate: int | None = None,
        max_fps: int | None = None,
        codec: str | None = None,
    ) -> dict:
        """Start the stream if it is not running (joining ``video`` or ``input`` does it too); returns ``status()``.
        ``codec`` (``h264``, ``hevc`` or ``mjpeg``: one the HMI produces, else ``ValueError``) picks the stream's codec
        for this lease; a running stream is restarted in it."""

    @abstractmethod
    def stop(self) -> dict:
        """Stop the stream, end every viewer's and release the device within a bounded time; returns ``status()``."""

    @abstractmethod
    def set_quality(self, level: str | int) -> dict:
        """Pin a quality level, or ``auto`` to follow the network. Raises ``ValueError`` if the HMI's quality is not
        adjustable.
        """

    @abstractmethod
    def start_recording(self, audio: bool = False, mic_audio: bool = False, max_seconds: float | None = None) -> dict:
        """Record the stream to an MP4 on the exporter (muxed from the stream's own H.264 or HEVC access units: no
        transcode). An MJPEG stream is not recorded: this fails, and ``hmi_info()["recording"]`` says so.
        """

    @abstractmethod
    def recording_status(self) -> dict:
        """What the recording is doing."""

    @abstractmethod
    def stop_recording(self) -> dict:
        """End the recording and finish its file; the bytes come from ``recording_download``."""

    @abstractmethod
    def recording_download(self) -> AbstractAsyncContextManager[Any]:
        """The finished recording's bytes (an MP4), then the end of the stream."""

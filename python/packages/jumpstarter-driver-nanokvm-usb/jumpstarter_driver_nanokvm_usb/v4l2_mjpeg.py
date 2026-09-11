"""Linux V4L2 MJPEG capture via mmap (single compression generation from UVC)."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import logging
import mmap
import os
import select
from typing import Any

logger = logging.getLogger(__name__)

V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_MEMORY_MMAP = 1
V4L2_FIELD_ANY = 0
V4L2_PIX_FMT_MJPEG = 0x47504A4D

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_NRSHIFT + _IOC_NRBITS + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS


def _ioc(dir_: int, typ: str, nr: int, size: int) -> int:
    return (
        (dir_ << _IOC_DIRSHIFT)
        | (ord(typ) & 0xFF) << _IOC_TYPESHIFT
        | (nr & 0xFF) << _IOC_NRSHIFT
        | (size & 0x3FFF) << _IOC_SIZESHIFT
    )


def _iowr(typ: str, nr: int, size: type[ctypes.Structure]) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, typ, nr, ctypes.sizeof(size))


def _iow(typ: str, nr: int, size: int) -> int:
    return _ioc(_IOC_WRITE, typ, nr, size)


class timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class v4l2_timecode(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("frames", ctypes.c_uint8),
        ("seconds", ctypes.c_uint8),
        ("minutes", ctypes.c_uint8),
        ("hours", ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class v4l2_pix_format(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixelformat", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("bytesperline", ctypes.c_uint32),
        ("sizeimage", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32),
        ("priv", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("ycbcr_enc", ctypes.c_uint32),
        ("quantization", ctypes.c_uint32),
        ("xfer_func", ctypes.c_uint32),
    ]


class v4l2_format(ctypes.Structure):
    class _Fmt(ctypes.Union):
        _fields_ = [
            ("pix", v4l2_pix_format),
            ("raw_data", ctypes.c_uint8 * 200),
        ]

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("fmt", _Fmt),
    ]


class v4l2_requestbuffers(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class v4l2_buffer(ctypes.Structure):
    class _M(ctypes.Union):
        _fields_ = [
            ("offset", ctypes.c_uint32),
            ("userptr", ctypes.c_ulong),
            ("planes", ctypes.c_void_p),
            ("fd", ctypes.c_int32),
        ]

    class _Reserved(ctypes.Union):
        _fields_ = [
            ("request_fd", ctypes.c_int32),
            ("reserved", ctypes.c_uint32),
        ]

    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("timestamp", timeval),
        ("timecode", v4l2_timecode),
        ("sequence", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("m", _M),
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("_reserved", _Reserved),
    ]


VIDIOC_S_FMT = _iowr("V", 5, v4l2_format)
VIDIOC_REQBUFS = _iowr("V", 8, v4l2_requestbuffers)
VIDIOC_QUERYBUF = _iowr("V", 9, v4l2_buffer)
VIDIOC_QBUF = _iowr("V", 15, v4l2_buffer)
VIDIOC_DQBUF = _iowr("V", 17, v4l2_buffer)
VIDIOC_STREAMON = _iow("V", 18, ctypes.sizeof(ctypes.c_int))
VIDIOC_STREAMOFF = _iow("V", 19, ctypes.sizeof(ctypes.c_int))


def _device_path(device: int | str) -> str:
    if isinstance(device, str):
        return device
    return f"/dev/video{device}"


def _ioctl(fd: int, request: int, arg) -> None:
    try:
        fcntl.ioctl(fd, request, arg)
    except OSError as exc:
        raise OSError(exc.errno, f"V4L2 ioctl 0x{request:08x} failed: {exc}") from exc


class V4L2MjpegCapture:
    """Capture compressed MJPEG frames directly from a V4L2 device."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._buffers: list[dict[str, Any]] = []
        self._width = 0
        self._height = 0

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self, device: int | str, width: int, height: int, fps: int) -> None:
        if self.is_open:
            self.close()

        path = _device_path(device)
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK, 0)

        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.fmt.pix.width = width
        fmt.fmt.pix.height = height
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG
        fmt.fmt.pix.field = V4L2_FIELD_ANY
        _ioctl(fd, VIDIOC_S_FMT, fmt)

        actual_w = int(fmt.fmt.pix.width)
        actual_h = int(fmt.fmt.pix.height)
        if actual_w != width or actual_h != height:
            logger.warning(
                "V4L2 MJPEG requested %sx%s, device negotiated %sx%s",
                width,
                height,
                actual_w,
                actual_h,
            )

        req = v4l2_requestbuffers()
        req.count = 4
        req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        req.memory = V4L2_MEMORY_MMAP
        _ioctl(fd, VIDIOC_REQBUFS, req)
        if req.count < 1:
            os.close(fd)
            raise ConnectionError(f"V4L2 REQBUFS failed for {path}")

        buffers: list[dict[str, Any]] = []
        for index in range(req.count):
            buf = v4l2_buffer()
            buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            buf.memory = V4L2_MEMORY_MMAP
            buf.index = index
            _ioctl(fd, VIDIOC_QUERYBUF, buf)
            mm = mmap.mmap(
                fd,
                buf.length,
                mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
                offset=buf.m.offset,
            )
            buffers.append({"buffer": buf, "mmap": mm})
            _ioctl(fd, VIDIOC_QBUF, buf)

        buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        _ioctl(fd, VIDIOC_STREAMON, buf_type)

        self._fd = fd
        self._buffers = buffers
        self._width = actual_w
        self._height = actual_h
        logger.info("V4L2 MJPEG passthrough on %s (%sx%s @ %sfps)", path, actual_w, actual_h, fps)

    def get_resolution(self) -> tuple[int, int]:
        return self._width, self._height

    def read_jpeg(self) -> bytes:
        if self._fd is None:
            raise ConnectionError("V4L2 device not open")

        while True:
            readable, _, _ = select.select([self._fd], [], [], 2.0)
            if not readable:
                raise ConnectionError("Timed out waiting for V4L2 frame")

            buf = v4l2_buffer()
            buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            buf.memory = V4L2_MEMORY_MMAP
            try:
                fcntl.ioctl(self._fd, VIDIOC_DQBUF, buf)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                raise

            mm = self._buffers[buf.index]["mmap"]
            data = mm[: buf.bytesused]
            _ioctl(self._fd, VIDIOC_QBUF, buf)
            return bytes(data)

    def discard_frames(self, count: int = 1) -> None:
        for _ in range(count):
            try:
                self.read_jpeg()
            except OSError:
                break

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            buf_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
            _ioctl(self._fd, VIDIOC_STREAMOFF, buf_type)
        except OSError:
            pass
        for entry in self._buffers:
            entry["mmap"].close()
        self._buffers.clear()
        os.close(self._fd)
        self._fd = None

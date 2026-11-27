"""Record the HMI's video to a fragmented MP4 file: the stream's own H.264 or HEVC access units, no transcode, no
ffmpeg. A Motion JPEG stream is not recorded.

* :mod:`~jumpstarter_driver_hmi.recording.mp4`: the muxer (Annex-B access units in, fragmented MP4 bytes out),
  standard library only.
* :mod:`~jumpstarter_driver_hmi.recording.recorder`: :class:`Recording`, one more viewer of the hub that writes the
  muxer's output to a file with limits in time and bytes.
"""

from .mp4 import Fmp4Muxer, PictureSizeChanged
from .recorder import (
    REASON_CODEC_CHANGED,
    REASON_ERROR,
    REASON_MAX_BYTES,
    REASON_MAX_SECONDS,
    REASON_STOP,
    REASON_STREAM_ENDED,
    Recording,
)

__all__ = [
    "REASON_CODEC_CHANGED",
    "REASON_ERROR",
    "REASON_MAX_BYTES",
    "REASON_MAX_SECONDS",
    "REASON_STOP",
    "REASON_STREAM_ENDED",
    "Fmp4Muxer",
    "PictureSizeChanged",
    "Recording",
]

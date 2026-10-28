"""Pinned A2DP/SBC stream parameters shared by the driver and the client.

The driver is the AVDTP *acceptor*: the DUT's sink picks a configuration out of
whatever the peer advertises. Advertising a single configuration rather than a
range of them means uploaded audio can be validated up front instead of failing
as noise on the speaker, and gives the client a fixed transcoding target.
"""

# As encoded in the SBC frame header, so the driver can check an uploaded file
# against what it advertised.
SBC_SAMPLING_FREQUENCY = 44100
SBC_CHANNEL_COUNT = 2
SBC_CHANNEL_MODE = 2  # stereo
SBC_ALLOCATION_METHOD = 0  # loudness
SBC_BLOCK_COUNT = 16
SBC_SUBBAND_COUNT = 8
SBC_MIN_BITPOOL = 2
SBC_MAX_BITPOOL = 53

# ffmpeg's SBC encoder fixes 16 blocks, 8 subbands and loudness allocation, but
# chooses the channel mode and bitpool from the bitrate, and exposes no option
# to override either (only -sbc_delay and -msbc). It emits joint stereo below
# ~192 kbps and plain stereo above, so the pinned mode above is stereo and the
# bitrate has to stay in that range. 320 kbps lands on bitpool 52, just inside
# the ceiling of 53 that A2DP defines for 44.1 kHz stereo; 328 kbps would give
# 54 and be rejected.
SBC_TARGET_BITRATE = "320k"

# Tracks are buffered in the exporter's memory. At the bitrate above this is
# roughly two hours of audio, well past anything a media test needs.
MAX_TRACK_BYTES = 256 * 1024 * 1024

# Budget for the client's ffmpeg conversion, scaled to the input size so a
# long recording is not cut off while a wedged ffmpeg still cannot hang the
# play command forever.
TRANSCODE_BASE_TIMEOUT = 60.0
TRANSCODE_SECONDS_PER_MIB = 6.0


def ffmpeg_sbc_args(source: str, destination: str) -> list[str]:
    """ffmpeg arguments that transcode ``source`` into the pinned SBC format."""
    return [
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        source,
        "-ar",
        str(SBC_SAMPLING_FREQUENCY),
        "-ac",
        str(SBC_CHANNEL_COUNT),
        "-b:a",
        SBC_TARGET_BITRATE,
        "-f",
        "sbc",
        destination,
    ]


def describe_sbc_format() -> str:
    """Human-readable form of the pinned format, for error messages."""
    return (
        f"{SBC_SAMPLING_FREQUENCY} Hz, stereo, {SBC_SUBBAND_COUNT} subbands, "
        f"{SBC_BLOCK_COUNT} blocks, loudness allocation, "
        f"bitpool {SBC_MIN_BITPOOL}-{SBC_MAX_BITPOOL}"
    )

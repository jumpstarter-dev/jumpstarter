"""The A2DP source: SBC capabilities, the packet pump and the AVDTP stream.

Everything here belongs to one AVDTP connection and to the wire format it
carries. What the peer *intends* to play lives in `player`; this module only
knows how to turn a `Track` into RTP packets and how to get a stream running.
"""

from dataclasses import dataclass
from typing import Any

import anyio
from bumble.a2dp import (
    A2DP_SBC_CODEC_TYPE,
    SBC_DUAL_CHANNEL_MODE,
    SBC_JOINT_STEREO_CHANNEL_MODE,
    SBC_MAX_FRAMES_IN_RTP_PAYLOAD,
    SBC_MONO_CHANNEL_MODE,
    SBC_SAMPLING_FREQUENCIES,
    SBC_SYNC_WORD,
    SbcFrame,
    SbcMediaCodecInformation,
)
from bumble.avdtp import (
    AVDTP_AUDIO_MEDIA_TYPE,
    MediaCodecCapabilities,
    MediaPacketPump,
    State,
)
from bumble.rtp import MediaPacket

from .errors import BtPeerError
from .media import (
    SBC_ALLOCATION_METHOD,
    SBC_BLOCK_COUNT,
    SBC_CHANNEL_MODE,
    SBC_MAX_BITPOOL,
    SBC_MIN_BITPOOL,
    SBC_SAMPLING_FREQUENCY,
    SBC_SUBBAND_COUNT,
    describe_sbc_format,
)

# Duration of a filler packet emitted while no track is playing.
SILENCE_PACKET_DURATION = 0.02

# Fallback when the AVDTP transport channel does not report a usable peer MTU.
DEFAULT_RTP_MTU = 672

# The L2CAP minimum; anything smaller cannot hold an RTP header plus a frame.
MIN_RTP_MTU = 48

# The fixed RTP header, ahead of the one-byte SBC media payload header.
RTP_HEADER_SIZE = 12


@dataclass(frozen=True)
class SbcConfig:
    """The bitpool range in force for a stream.

    Either side may have chosen it: the peer when it initiates
    SET_CONFIGURATION, the DUT when it does. `A2dpSession.prepare` reports
    whichever was agreed, so an upload is always checked against the range
    the sink will actually decode.
    """

    min_bitpool: int
    max_bitpool: int

    def describe(self) -> str:
        return f"{self.min_bitpool}-{self.max_bitpool}"

    def narrowed_to(self, other: "SbcConfig") -> "SbcConfig":
        """The range both this and `other` can honour.

        An intersection, not a replacement. The spec allows a bitpool up to
        250 and the peer pins 53, so a sink's range may be *wider* as well as
        narrower; taking the sink's outright would let the peer offer, and
        `Track.check` then accept, audio the peer never advertised it could
        send.
        """
        narrowed = SbcConfig(
            max(self.min_bitpool, other.min_bitpool),
            min(self.max_bitpool, other.max_bitpool),
        )
        if narrowed.min_bitpool > narrowed.max_bitpool:
            raise BtPeerError(
                f"the DUT's SBC sink wants bitpool {other.describe()}, which does not "
                f"overlap the {self.describe()} the peer advertises; "
                "there is no bitpool both sides accept"
            )
        return narrowed


# What the peer offers before any stream has been configured.
ADVERTISED = SbcConfig(SBC_MIN_BITPOOL, SBC_MAX_BITPOOL)


def _sbc_range(capabilities) -> SbcConfig | None:
    """The SBC bitpool range carried by a capability list, if it has one."""
    for capability in capabilities:
        if not isinstance(capability, MediaCodecCapabilities):
            continue
        info = capability.media_codec_information
        if isinstance(info, SbcMediaCodecInformation):
            return SbcConfig(info.minimum_bitpool_value, info.maximum_bitpool_value)
    return None


def _is_sbc(capability) -> bool:
    return isinstance(capability, MediaCodecCapabilities) and isinstance(
        capability.media_codec_information, SbcMediaCodecInformation
    )


def parse_sbc(data: bytes) -> tuple[SbcFrame, ...]:
    """Split an upload into SBC frames, rejecting anything that is not.

    Parsed here rather than with bumble's SbcParser, which stops quietly at
    the first bad sync word and pads nothing out when the last frame is
    short: a file with garbage after its first minute, or one cut off
    mid-frame, would be accepted and then sent as noise or not at all.
    """
    frames: list[SbcFrame] = []
    offset = 0
    while offset < len(data):
        header = data[offset : offset + 4]
        if len(header) < 4 or header[0] != SBC_SYNC_WORD:
            if not frames:
                raise BtPeerError(
                    "uploaded file is not SBC audio (no valid frame header found); "
                    f"expected {describe_sbc_format()}"
                )
            raise BtPeerError(f"SBC frame {len(frames)} at byte {offset} has no valid frame header")

        blocks = 4 * (1 + ((header[1] >> 4) & 3))
        channel_mode = (header[1] >> 2) & 3
        channels = 1 if channel_mode == SBC_MONO_CHANNEL_MODE else 2
        subbands = 8 if header[1] & 1 else 4
        bitpool = header[2]
        length = 4 + (4 * subbands * channels) // 8
        if channel_mode in (SBC_MONO_CHANNEL_MODE, SBC_DUAL_CHANNEL_MODE):
            length += (blocks * channels * bitpool) // 8
        else:
            joint = 1 if channel_mode == SBC_JOINT_STEREO_CHANNEL_MODE else 0
            length += (joint * subbands + blocks * bitpool) // 8

        if offset + length > len(data):
            raise BtPeerError(
                f"SBC frame {len(frames)} at byte {offset} is truncated: "
                f"{len(data) - offset} of {length} bytes"
            )
        frames.append(
            SbcFrame(
                SBC_SAMPLING_FREQUENCIES[(header[1] >> 6) & 3],
                blocks,
                channel_mode,
                (header[1] >> 1) & 1,
                subbands,
                bitpool,
                data[offset : offset + length],
            )
        )
        offset += length
    if not frames:
        raise BtPeerError("uploaded track is empty")
    return tuple(frames)


class Track:
    """An uploaded SBC file, and how far into it the stream has got.

    The frames are parsed once, at upload, and held in memory so that looping
    can rewind without re-uploading and the packet source never blocks on
    disk. How far the stream has got is one index into them. It is not a
    cached packet generator: that would fix the MTU of whichever media
    channel first read it, and a track outlives its channel when it is
    queued, paused or carried across a reconnect.
    """

    def __init__(self, name: str, data: bytes, loop: bool = False):
        self.name = name
        self.loop = loop
        self.frames = parse_sbc(data)
        self.size = len(data)
        self._next = 0
        self._consumed = 0

    @property
    def position(self) -> int:
        """Bytes of the file already handed to the stream."""
        return self._consumed

    def check(self, config: SbcConfig) -> None:
        """Reject a track the sink was never offered; see `check_sbc_frame`."""
        for index, frame in enumerate(self.frames):
            check_sbc_frame(frame, config=config, index=index)

    def next_payload(self, mtu: int) -> tuple[bytes, float] | None:
        """The next RTP payload for a channel of `mtu`, and the audio time it holds.

        As many whole frames as fit, up to the 15 the SBC payload header can
        count, and always at least one. None once every frame has been sent;
        the last payload goes out however few frames it holds.
        """
        if self._next == len(self.frames):
            return None
        budget = mtu - RTP_HEADER_SIZE - 1
        taken: list[SbcFrame] = []
        size = 0
        while self._next < len(self.frames) and len(taken) < SBC_MAX_FRAMES_IN_RTP_PAYLOAD:
            frame = self.frames[self._next]
            if taken and size + len(frame.payload) > budget:
                break
            taken.append(frame)
            size += len(frame.payload)
            self._next += 1
        self._consumed += size
        payload = bytes([len(taken)]) + b"".join(frame.payload for frame in taken)
        return payload, sum(frame.duration for frame in taken)

    def rewind(self) -> None:
        self._next = 0
        self._consumed = 0


def sbc_capabilities(config: SbcConfig = ADVERTISED) -> MediaCodecCapabilities:
    """The single SBC configuration the peer advertises, over `config`'s range.

    The peer is the AVDTP acceptor, so the sink picks a configuration out of
    whatever is offered. Advertising exactly one means play() can reject
    mismatched audio up front instead of letting it reach the speakers as
    noise.

    Takes a range rather than reading the pinned constants, because the offer
    for a given stream is narrowed to what its sink accepts. It has to be a
    fresh object each time: MediaCodecCapabilities serializes itself once, in
    __init__, and everything downstream reads that cached buffer.
    """
    S = SbcMediaCodecInformation
    return MediaCodecCapabilities(
        media_type=AVDTP_AUDIO_MEDIA_TYPE,
        media_codec_type=A2DP_SBC_CODEC_TYPE,
        media_codec_information=S(
            sampling_frequency=S.SamplingFrequency.SF_44100,
            channel_mode=S.ChannelMode.STEREO,
            block_length=S.BlockLength.BL_16,
            subbands=S.Subbands.S_8,
            allocation_method=S.AllocationMethod.LOUDNESS,
            minimum_bitpool_value=config.min_bitpool,
            maximum_bitpool_value=config.max_bitpool,
        ),
    )


async def _no_packets():
    """Stands in until START supplies the real source."""
    return
    yield  # pragma: no cover


class StartPump(MediaPacketPump):
    """Builds the packet stream at START, once the RTP channel exists.

    The MTU that bounds an RTP payload belongs to the media transport
    channel, which is only created when the stream opens. Taking it from the
    signalling channel at connection time reads a different channel's value.
    """

    def __init__(self, session: "A2dpSession"):
        super().__init__(_no_packets())
        self._session = session

    async def start(self, rtp_channel) -> None:
        self.packets = self._session.packets(getattr(rtp_channel, "peer_mtu", None))
        await super().start(rtp_channel)


class A2dpSession:
    """Everything belonging to one AVDTP connection.

    Scoped to the connection rather than the driver, so a closed channel
    takes its protocol, source and stream with it instead of leaving stale
    objects behind for the next play() to operate on.

    The track itself stays with the player: it is playback intent, it can be
    set before any connection exists, and it should survive a reconnect.
    """

    def __init__(self, protocol, get_track, on_exhausted, emit):
        self.protocol = protocol
        self.mtu = DEFAULT_RTP_MTU
        self._get_track = get_track
        self._on_exhausted = on_exhausted
        self._emit = emit
        # play() configures the stream outside the player's lock, and so can
        # the queued resume that runs when the channel arrives.
        self._setup = anyio.Lock()
        self.source = protocol.add_source(sbc_capabilities(), StartPump(self))

    @property
    def is_streaming(self) -> bool:
        stream = self.source.stream
        return stream is not None and stream.state == State.STREAMING

    # -- packets ---------------------------------------------------------

    def packets(self, mtu):
        """Start a fresh packet stream sized to the media channel's MTU."""
        self.mtu = mtu if isinstance(mtu, int) and mtu >= MIN_RTP_MTU else DEFAULT_RTP_MTU
        self._emit("rtp_channel_open", {"mtu": self.mtu})
        return self._media_source()

    def _make_packet(self, sequence_number: int, timestamp_seconds: float, payload: bytes) -> MediaPacket:
        packet = MediaPacket(
            2, 0, 0, 0, sequence_number, int(1000 * timestamp_seconds), 0, [], 96, payload
        )
        packet.timestamp_seconds = timestamp_seconds
        return packet

    async def _media_source(self):
        """Packet source backing the stream for as long as it is started.

        Emits filler while nothing is playing and the track's packets
        otherwise, so a track can be swapped without renegotiating.
        MediaPacketPump paces itself against packet.timestamp_seconds relative
        to the first packet it sees, so timestamps have to keep rising for the
        life of the stream. One stream-wide clock advances by the audio each
        packet carries, across filler, track changes and loops; were it to
        restart with a track, the pump would see time jump backwards and
        flush the whole file at wire speed.
        """
        sequence_number = 0
        timestamp_seconds = 0.0

        while True:
            track = self._get_track()

            if track is None:
                # No sleep here: the pump already waits until each packet's
                # timestamp before writing it, so sleeping as well would make
                # real time run ahead of the stream clock by the sleep
                # overhead. That gap is invisible while idle, but the pump
                # makes it up the instant a track starts by flushing packets
                # at wire speed, which overruns the sink's buffer.
                yield self._make_packet(sequence_number, timestamp_seconds, b"\x00")
                sequence_number = (sequence_number + 1) & 0xFFFF
                timestamp_seconds += SILENCE_PACKET_DURATION
                continue

            chunk = track.next_payload(self.mtu)
            if chunk is None:
                # Rewinds for a loop, or drops the track so the next pass
                # falls through to filler.
                self._on_exhausted(track)
                continue

            payload, duration = chunk
            yield self._make_packet(sequence_number, timestamp_seconds, payload)
            sequence_number = (sequence_number + 1) & 0xFFFF
            timestamp_seconds += duration

    # -- stream lifecycle ------------------------------------------------

    async def _open_stream(self):
        """Return a stream ready to be started, creating one if needed.

        A stream left IDLE by a close cannot be started again, so it needs
        recreating exactly as if there had never been one. A stream in any
        other state was configured by one side or the other and is reused.

        Serialized: two callers that both found no stream would both
        configure one, and the second finds the sink already in use.
        """
        async with self._setup:
            stream = self.source.stream
            if stream is not None and stream.state != State.IDLE:
                return stream

            await self.protocol.discover_remote_endpoints()
            sink = self.protocol.find_remote_sink_by_codec(AVDTP_AUDIO_MEDIA_TYPE, A2DP_SBC_CODEC_TYPE)
            if sink is None:
                raise BtPeerError(
                    "the DUT exposes no free SBC audio sink endpoint; "
                    "is Bluetooth media enabled on the head unit?"
                )
            # Before create_stream, which is what sends SET_CONFIGURATION.
            self._offer_to_sink(sink)
            return await self.protocol.create_stream(self.source, sink)

    async def prepare(self) -> SbcConfig:
        """Configure the stream if needed, and report the bitpool agreed.

        The range is read back from the endpoint's configuration rather than
        from the sink's capabilities, because either side may have set it.
        When the DUT sends SET_CONFIGURATION itself -- a head unit that drives
        the negotiation rather than waiting -- the stream is already
        configured by the time play() runs, `_open_stream` reuses it and
        `_offer_to_sink` never sees the sink. Reading the configuration covers
        both directions.

        It is intersected with what the peer advertises even so: on that
        DUT-initiated path nothing has narrowed the offer, and bumble accepts
        whatever range the head unit asked for, which may fall outside the
        pinned one.
        """
        await self._open_stream()
        configured = _sbc_range(self.source.configuration)
        if configured is None:
            raise BtPeerError("the AVDTP stream was configured without an SBC codec")

        config = ADVERTISED.narrowed_to(configured)
        self._emit("stream_configured", {
            "minimum_bitpool": config.min_bitpool,
            "maximum_bitpool": config.max_bitpool,
        })
        return config

    async def start(self) -> dict[str, Any]:
        """Put the stream into STREAMING, initiating it if necessary.

        A2DP sources drive the stream: a phone configures, opens and starts
        it when the user hits play, and the sink renders what arrives. The
        DUT connects the signalling channel and then waits, so nothing is
        pumped until the peer initiates here.
        """
        stream = await self._open_stream()
        if stream.state == State.STREAMING:
            return {"streaming": True, "reason": "already streaming"}
        # start() opens the media transport channel itself when CONFIGURED.
        await stream.start()
        return {"streaming": True, "reason": "started by the peer"}

    async def suspend(self) -> bool:
        """Send AVDTP SUSPEND, leaving the stream OPEN and resumable."""
        stream = self.source.stream
        if stream is None or stream.state != State.STREAMING:
            return False
        await stream.stop()
        return True

    def _offer_to_sink(self, sink) -> None:
        """Narrow this stream's offer to the range both sides accept.

        The sink rejects a configuration whose bitpool range is wider than
        its own, so the offer has to be narrowed before SET_CONFIGURATION.
        Raises if the two ranges do not overlap at all, which is better found
        here than as a rejected SET_CONFIGURATION or, worse, as noise.

        Only reached when the peer initiates; when the DUT configures the
        stream it picks the range itself, and `prepare` reads it back.

        Two things this deliberately does not do:

        - It does not mutate the existing capability.
          MediaCodecCapabilities caches its serialized bytes in __init__ and
          `ServiceCapabilities.serialize_capabilities` emits that buffer, so
          writing to `media_codec_information` narrows what this driver
          believes it offered without changing a byte on the wire.
        - It does not touch `source.capabilities`. bumble aliases
          `configuration` to the same list, and capabilities answer
          GET_CAPABILITIES -- they are what the peer *can* do, which is still
          the full advertised range. Only the offer for this stream narrows.
        """
        remote = _sbc_range(sink.capabilities)
        if remote is None:
            return
        offer = ADVERTISED.narrowed_to(remote)
        self.source.configuration = [
            sbc_capabilities(offer) if _is_sbc(capability) else capability
            for capability in self.source.capabilities
        ]
        self._emit("sink_bitpool", {
            "sink_minimum": remote.min_bitpool,
            "sink_maximum": remote.max_bitpool,
            "offered_minimum": offer.min_bitpool,
            "offered_maximum": offer.max_bitpool,
        })


def check_sbc_frame(frame: SbcFrame, config: SbcConfig = ADVERTISED, index: int = 0) -> None:
    """Reject audio the DUT's sink was never offered.

    config is the negotiated bitpool range once a stream exists, which may be
    narrower than the advertised one.
    """
    mismatches = []
    if frame.sampling_frequency != SBC_SAMPLING_FREQUENCY:
        mismatches.append(f"sampling frequency {frame.sampling_frequency} Hz")
    if frame.channel_mode != SBC_CHANNEL_MODE:
        mismatches.append(f"channel mode {frame.channel_mode} (expected stereo)")
    if frame.block_count != SBC_BLOCK_COUNT:
        mismatches.append(f"{frame.block_count} blocks")
    if frame.subband_count != SBC_SUBBAND_COUNT:
        mismatches.append(f"{frame.subband_count} subbands")
    if frame.allocation_method != SBC_ALLOCATION_METHOD:
        mismatches.append(f"allocation method {frame.allocation_method} (expected loudness)")
    if not config.min_bitpool <= frame.bitpool <= config.max_bitpool:
        mismatches.append(f"bitpool {frame.bitpool} (sink accepts {config.describe()})")

    if mismatches:
        where = "SBC file" if index == 0 else f"SBC frame {index}"
        raise BtPeerError(
            f"{where} does not match the advertised stream configuration "
            f"({describe_sbc_format()}): got {', '.join(mismatches)}"
        )

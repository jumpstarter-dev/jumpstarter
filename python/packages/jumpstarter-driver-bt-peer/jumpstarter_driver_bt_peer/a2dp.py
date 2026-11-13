"""The A2DP source: SBC capabilities, the packet pump and the AVDTP stream.

Everything here belongs to one AVDTP connection and to the wire format it
carries. What the peer *intends* to play lives in `player`; this module only
knows how to turn a `Track` into RTP packets and how to get a stream running.
"""

from dataclasses import dataclass
from typing import Any

from bumble.a2dp import (
    A2DP_SBC_CODEC_TYPE,
    SbcFrame,
    SbcMediaCodecInformation,
    SbcPacketSource,
    SbcParser,
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
        `validate_sbc` then accept, audio the peer never advertised it could
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


class ByteReader:
    """An async ``read(n)`` over an in-memory buffer, as bumble's parsers expect."""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    async def read(self, byte_count: int) -> bytes:
        chunk = self.data[self.position : self.position + byte_count]
        self.position += len(chunk)
        return chunk


class Track:
    """An uploaded SBC file being streamed to the DUT.

    The whole file is held in memory so that looping can rewind without
    re-uploading, and so the packet source never blocks the event loop on disk.

    The track does not own an MTU: that belongs to the media transport
    channel, which may not exist yet when a track is queued.
    """

    def __init__(self, name: str, data: bytes, loop: bool = False):
        self.name = name
        self.data = data
        self.loop = loop
        self.last_timestamp = 0.0
        self._reader = ByteReader(data)
        self._packets = None

    @property
    def position(self) -> int:
        return self._reader.position

    def packets(self, mtu: int):
        """The packet generator, built on first use so it can size to the MTU.

        SbcPacketSource.packets is a property that builds a fresh generator on
        every access, so it has to be held rather than re-read each time.
        """
        if self._packets is None:
            self._packets = SbcPacketSource(self._reader.read, mtu).packets
        return self._packets

    def rewind(self) -> None:
        self._reader = ByteReader(self.data)
        self.last_timestamp = 0.0
        self._packets = None


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
        life of the stream. Each track's own timestamps restart at zero, so
        per-track deltas are accumulated into a stream-wide clock rather than
        passed through; otherwise the pump would see time jump backwards on a
        track change and flush the whole file at wire speed.
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

            try:
                packet = await anext(track.packets(self.mtu))
            except StopAsyncIteration:
                # Rewinds for a loop, or drops the track so the next pass
                # falls through to filler.
                self._on_exhausted(track)
                continue

            delta = packet.timestamp_seconds - track.last_timestamp
            track.last_timestamp = packet.timestamp_seconds
            if delta > 0:
                timestamp_seconds += delta

            packet.sequence_number = sequence_number
            packet.timestamp_seconds = timestamp_seconds
            packet.timestamp = int(1000 * timestamp_seconds)
            sequence_number = (sequence_number + 1) & 0xFFFF
            yield packet

    # -- stream lifecycle ------------------------------------------------

    async def _open_stream(self):
        """Return a stream ready to be started, creating one if needed.

        A stream left IDLE by a close cannot be started again, so it needs
        recreating exactly as if there had never been one. A stream in any
        other state was configured by one side or the other and is reused.
        """
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


async def validate_sbc(data: bytes, config: SbcConfig = ADVERTISED) -> SbcFrame:
    """Check every frame of an upload, returning the first.

    Every frame is checked, not just the first. SbcPacketSource concatenates
    later frames without looking at their parameters, so a file whose first
    frame matches and whose later frames do not would be transmitted under a
    configuration the sink never agreed to. Parsing is header-only, so the
    walk costs little even for a long track.
    """
    first: SbcFrame | None = None
    index = 0
    async for frame in SbcParser(ByteReader(data).read).frames:
        check_sbc_frame(frame, config=config, index=index)
        if first is None:
            first = frame
        index += 1

    if first is None:
        raise BtPeerError(
            "uploaded file is not SBC audio (no valid frame header found); "
            f"expected {describe_sbc_format()}"
        )
    return first


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

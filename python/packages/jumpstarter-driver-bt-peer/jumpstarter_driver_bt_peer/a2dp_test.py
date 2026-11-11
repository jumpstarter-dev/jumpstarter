from unittest.mock import AsyncMock, MagicMock

import pytest
from bumble.avdtp import State

from .a2dp import (
    ADVERTISED,
    DEFAULT_RTP_MTU,
    A2dpSession,
    SbcConfig,
    Track,
    validate_sbc,
)
from .avrcp import TrackMetadata
from .conftest import fake_protocol, make_sbc_file, sbc_capability, session_for, sink_with_bitpool
from .driver import BtPeer, BtPeerError
from .player import Playing

# -- SBC validation ------------------------------------------------------


@pytest.mark.anyio
async def test_validate_sbc_parses_pinned_format():
    frame = await validate_sbc(make_sbc_file(4))
    assert frame.sampling_frequency == 44100
    assert frame.channel_mode == 2
    assert frame.block_count == 16
    assert frame.subband_count == 8
    assert frame.allocation_method == 0


@pytest.mark.anyio
async def test_validate_sbc_rejects_non_sbc():
    with pytest.raises(BtPeerError, match="not SBC audio"):
        await validate_sbc(b"ID3\x04 this is not audio")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"sf_index": 3}, "48000 Hz"),
        ({"channel_mode": 0}, "channel mode"),
        ({"bitpool": 90}, "bitpool 90"),
    ],
)
async def test_validate_sbc_rejects_mismatches(kwargs, expected):
    with pytest.raises(BtPeerError, match=expected):
        await validate_sbc(make_sbc_file(4, **kwargs))


@pytest.mark.anyio
async def test_validate_sbc_checks_every_frame_not_just_the_first():
    """SbcPacketSource concatenates later frames without re-checking them."""
    good = make_sbc_file(4)
    mixed = good + make_sbc_file(1, sf_index=3) + good

    with pytest.raises(BtPeerError, match="48000 Hz") as excinfo:
        await validate_sbc(mixed)

    assert "frame 4" in str(excinfo.value), excinfo.value


@pytest.mark.anyio
async def test_validate_sbc_honours_the_negotiated_bitpool_ceiling():
    """A sink may accept less than the advertised maximum."""
    data = make_sbc_file(4, bitpool=52)

    await validate_sbc(data, ADVERTISED)

    with pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await validate_sbc(data, SbcConfig(2, 35))


# -- tracks --------------------------------------------------------------


@pytest.mark.anyio
async def test_track_rewind_resets_data_and_generator():
    track = Track(name="t", data=make_sbc_file(10))
    first = track.packets(672)
    await track._reader.read(50)
    assert track.position == 50

    track.rewind()

    assert track.position == 0
    assert track.last_timestamp == 0.0
    assert track.packets(672) is not first, "rewind must build a fresh generator"


def test_track_packet_generator_is_held_not_rebuilt():
    """SbcPacketSource.packets is a property that yields a new generator."""
    track = Track(name="t", data=make_sbc_file(10))
    assert track.packets(672) is track.packets(672)


# -- the packet source ---------------------------------------------------


def _play(peer, data: bytes, loop: bool = False) -> Track:
    """Install a playing track without going through the AVDTP stream."""
    track = Track(name="song.sbc", data=data, loop=loop)
    peer._player.state = Playing(track, TrackMetadata())
    return track


@pytest.mark.anyio
async def test_media_source_timestamps_never_go_backwards():
    """MediaPacketPump paces off timestamps, so a track swap must not rewind them."""
    peer = BtPeer()
    source = session_for(peer)._media_source()
    timestamps = []

    for _ in range(3):
        timestamps.append((await source.__anext__()).timestamp_seconds)

    _play(peer, make_sbc_file(200))
    while peer._player.track is not None:
        timestamps.append((await source.__anext__()).timestamp_seconds)

    # And back to silence once the track drains.
    timestamps.append((await source.__anext__()).timestamp_seconds)

    assert timestamps == sorted(timestamps)
    assert timestamps[-1] > timestamps[2]
    assert any(e["type"] == "track_finished" for e in peer._events)


@pytest.mark.anyio
async def test_media_source_loops_without_rewinding_timestamps():
    peer = BtPeer()
    source = session_for(peer)._media_source()
    _play(peer, make_sbc_file(30), loop=True)

    timestamps = [(await source.__anext__()).timestamp_seconds for _ in range(120)]

    assert timestamps == sorted(timestamps)
    assert peer._player.track is not None, "loop mode should keep the track active"
    assert any(e["type"] == "track_looped" for e in peer._events)


@pytest.mark.anyio
async def test_rtp_mtu_comes_from_the_media_channel_not_signalling():
    """The signalling channel's MTU does not bound RTP payloads.

    The media transport channel is a separate L2CAP channel created when the
    stream opens, and only its MTU may size a packet.
    """
    peer = BtPeer()
    protocol = fake_protocol(signalling_mtu=1024)
    peer._on_avdtp_connection(protocol)
    session = peer._session
    assert session is not None

    pump = protocol.add_source.call_args.args[1]
    await pump.start(MagicMock(peer_mtu=345))

    assert session.mtu == 345, "the pump took the wrong channel's MTU"
    assert any(e.get("mtu") == 345 for e in peer._events)


@pytest.mark.anyio
@pytest.mark.parametrize("bad_mtu", [None, "672", 4, MagicMock()])
async def test_implausible_media_mtu_falls_back(bad_mtu):
    peer = BtPeer()
    protocol = fake_protocol()
    peer._on_avdtp_connection(protocol)
    session = peer._session
    assert session is not None

    pump = protocol.add_source.call_args.args[1]
    await pump.start(MagicMock(peer_mtu=bad_mtu))

    assert session.mtu == DEFAULT_RTP_MTU
    # _emit serializes to JSON, so a non-int MTU would blow up there.
    assert any(e["type"] == "rtp_channel_open" for e in peer._events)


# -- stream lifecycle ----------------------------------------------------


def _session(**kwargs) -> tuple[A2dpSession, MagicMock]:
    protocol = fake_protocol(**kwargs)
    return (
        A2dpSession(protocol, get_track=lambda: None, on_exhausted=lambda _t: None, emit=lambda *_a: None),
        protocol,
    )


@pytest.mark.anyio
async def test_start_is_a_noop_when_already_streaming():
    session, protocol = _session()
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING

    assert await session.start() == {"streaming": True, "reason": "already streaming"}
    protocol.discover_remote_endpoints.assert_not_called()
    protocol.stream.start.assert_not_awaited()


@pytest.mark.anyio
async def test_start_initiates_the_stream():
    """The DUT opens AVDTP signalling and waits; the source has to start it."""
    session, protocol = _session()

    assert await session.start() == {"streaming": True, "reason": "started by the peer"}
    protocol.discover_remote_endpoints.assert_awaited_once()
    protocol.create_stream.assert_awaited_once()
    protocol.stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_a_stream_left_idle_by_a_close_is_recreated():
    """start() on an IDLE stream raises; it has to be configured afresh."""
    session, protocol = _session()
    stale = AsyncMock()
    stale.state = State.IDLE
    protocol.source.stream = stale

    assert await session.start() == {"streaming": True, "reason": "started by the peer"}
    stale.start.assert_not_awaited()
    protocol.discover_remote_endpoints.assert_awaited_once()
    protocol.create_stream.assert_awaited_once()
    protocol.stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_an_open_stream_is_reused_rather_than_recreated():
    session, protocol = _session()
    open_stream = AsyncMock()
    open_stream.state = State.OPEN
    protocol.source.stream = open_stream

    assert await session.start() == {"streaming": True, "reason": "started by the peer"}
    protocol.discover_remote_endpoints.assert_not_called()
    open_stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_starting_without_a_sink_endpoint():
    session, protocol = _session()
    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)

    with pytest.raises(BtPeerError, match="no free SBC audio sink"):
        await session.start()


@pytest.mark.anyio
async def test_suspend_only_stops_a_streaming_stream():
    session, protocol = _session()
    protocol.source.stream = protocol.stream

    protocol.stream.state = State.OPEN
    assert await session.suspend() is False
    protocol.stream.stop.assert_not_awaited()

    protocol.stream.state = State.STREAMING
    assert await session.suspend() is True
    protocol.stream.stop.assert_awaited_once()


# -- bitpool negotiation -------------------------------------------------


@pytest.mark.anyio
async def test_prepare_clamps_the_offer_to_the_sink_the_peer_found():
    """The sink rejects a range wider than its own, so the offer is narrowed."""
    session, protocol = _session(sink=sink_with_bitpool(2, 35))

    config = await session.prepare()

    offered = protocol.source.capabilities[0].media_codec_information
    assert (offered.minimum_bitpool_value, offered.maximum_bitpool_value) == (2, 35)
    assert config == SbcConfig(2, 35)


@pytest.mark.anyio
async def test_prepare_reads_the_range_the_dut_configured():
    """A head unit may send SET_CONFIGURATION itself rather than waiting.

    The stream is then already configured, so `_open_stream` reuses it and
    never sees the sink's capabilities. The agreed range still has to come
    back, or an upload would be checked against the advertised ceiling and
    audio the sink cannot decode would be streamed at it.
    """
    session, protocol = _session()
    # What bumble's LocalStreamEndPoint.on_set_configuration_command does.
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.CONFIGURED
    protocol.source.configuration = [sbc_capability(2, 35)]

    config = await session.prepare()

    protocol.discover_remote_endpoints.assert_not_called()
    assert config == SbcConfig(2, 35), "the DUT's own configuration was ignored"


@pytest.mark.anyio
async def test_prepare_rejects_a_stream_configured_without_sbc():
    session, protocol = _session()
    protocol.source.configuration = []

    with pytest.raises(BtPeerError, match="configured without an SBC codec"):
        await session.prepare()

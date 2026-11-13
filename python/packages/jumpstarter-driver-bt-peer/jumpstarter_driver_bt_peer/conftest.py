"""Shared fakes for the bt-peer tests.

The real objects here are an HCI controller and a head unit, so every test
stands on a hand-built AVDTP protocol rather than a live stack.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bumble.a2dp import SbcMediaCodecInformation
from bumble.avdtp import MediaCodecCapabilities, State

from .a2dp import A2dpSession, sbc_capabilities
from .avrcp import PeerDelegate, Reporter
from .driver import BtPeer


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_sbc_file(frame_count: int, bitpool: int = 52, sf_index: int = 2, channel_mode: int = 2) -> bytes:
    """Synthesize an SBC file; defaults match the configuration the peer advertises."""
    header_byte = (sf_index << 6) | (3 << 4) | (channel_mode << 2) | (0 << 1) | 1
    channels = 1 if channel_mode == 0 else 2
    subbands, blocks = 8, 16
    length = 4 + (4 * subbands * channels) // 8
    if channel_mode in (0, 1):
        length += (blocks * channels * bitpool) // 8
    else:
        length += ((1 if channel_mode == 3 else 0) * subbands + blocks * bitpool) // 8
    return (bytes([0x9C, header_byte, bitpool, 0x00]) + bytes(length - 4)) * frame_count


def sbc_capability(minimum: int, maximum: int):
    """A MediaCodecCapabilities carrying just a bitpool range."""
    info = MagicMock(spec=SbcMediaCodecInformation)
    info.minimum_bitpool_value = minimum
    info.maximum_bitpool_value = maximum
    capability = MagicMock(spec=MediaCodecCapabilities)
    capability.media_codec_information = info
    return capability


def sink_with_bitpool(minimum: int, maximum: int):
    sink = MagicMock()
    sink.capabilities = [sbc_capability(minimum, maximum)]
    return sink


def fake_protocol(signalling_mtu: int = 1024, sink=None, stream_state=State.CONFIGURED):
    """An AVDTP protocol whose add_source records the pump the session builds.

    signalling_mtu is deliberately distinct from any media channel MTU a test
    supplies, so a test fails if the wrong channel is read.

    The source carries real capability objects, aliased between
    `capabilities` and `configuration` exactly as bumble's LocalSource does.
    Mocks would hide that MediaCodecCapabilities serializes itself once in
    __init__, which is the whole reason the offer is rebuilt rather than
    edited.
    """
    source = MagicMock()
    source.stream = None
    source.capabilities = [sbc_capabilities()]
    source.configuration = source.capabilities

    stream = AsyncMock()
    stream.state = stream_state

    protocol = MagicMock()
    protocol.l2cap_channel.peer_mtu = signalling_mtu
    protocol.add_source = MagicMock(return_value=source)
    protocol.discover_remote_endpoints = AsyncMock()
    protocol.find_remote_sink_by_codec = MagicMock(
        return_value=sink if sink is not None else sink_with_bitpool(2, 53)
    )
    protocol.create_stream = AsyncMock(return_value=stream)
    protocol.stream = stream
    protocol.source = source
    return protocol


def make_transport():
    transport = MagicMock()
    transport.source = MagicMock()
    transport.sink = MagicMock()
    transport.close = AsyncMock()
    return transport


def make_device(address="DA:4C:10:DE:00:01"):
    device = MagicMock()
    device.public_address = address
    device.power_on = AsyncMock()
    device.power_off = AsyncMock()
    device.set_discoverable = AsyncMock()
    device.set_connectable = AsyncMock()
    device.on = MagicMock()
    device.remove_listener = MagicMock()
    device.connect = AsyncMock()
    device.sdp_service_records = {}
    device.pairing_config_factory = None
    return device


@contextlib.contextmanager
def patched_stack(device=None, transport=None):
    """Patch out the bumble objects start_peer builds."""
    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=transport if transport is not None else make_transport()),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=device if device is not None else make_device(),
        ) as device_cls,
        patch("jumpstarter_driver_bt_peer.driver.Listener") as listener_cls,
    ):
        listener_cls.for_device.return_value = MagicMock()
        yield device_cls


def running_peer(**kwargs) -> BtPeer:
    """A peer past start_peer, with an AVRCP target but no AVDTP connection."""
    peer = BtPeer(**kwargs)
    peer._device = MagicMock()
    peer._avrcp_delegate = PeerDelegate(now_playing=peer._player.now_playing)
    peer._avrcp = MagicMock()
    peer._reporter = Reporter(peer._avrcp_delegate, peer._avrcp, peer.logger)
    return peer


def delegate_of(peer) -> PeerDelegate:
    """The peer's AVRCP delegate, narrowed from the Optional field."""
    delegate = peer._avrcp_delegate
    assert delegate is not None, "the peer has no AVRCP target"
    return delegate


def avrcp_mock(peer) -> MagicMock:
    """The stand-in AVRCP protocol `running_peer` installed."""
    protocol = peer._avrcp
    assert isinstance(protocol, MagicMock)
    return protocol


def peer_with_session(**kwargs):
    """A running peer with an AVDTP session already established."""
    peer = running_peer()
    protocol = fake_protocol(**kwargs)
    peer._on_avdtp_connection(protocol)
    return peer, protocol


def session_for(peer) -> A2dpSession:
    """A session bound to `peer`, without needing a real AVDTP protocol."""
    return A2dpSession(
        fake_protocol(),
        get_track=lambda: peer._player.track,
        on_exhausted=peer._player.on_track_exhausted,
        emit=peer._emit,
    )


class FakeResource:
    """Stands in for the client upload stream handed to play()."""

    def __init__(self, data: bytes, chunk: int = 4096):
        self._chunks = [data[i : i + chunk] for i in range(0, len(data), chunk)]

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        for chunk in self._chunks:
            yield chunk


def patch_resource(payload: bytes):
    """Patch Driver.resource so play() sees `payload` as the client upload."""

    @contextlib.asynccontextmanager
    async def _resource(self, handle, timeout=7200):
        yield FakeResource(payload)

    return patch.object(BtPeer, "resource", _resource)


async def settle(times: int = 30, until=None) -> None:
    """Let spawned tasks run; `_on_avdtp_connection` resumes a track on one."""
    import anyio.lowlevel

    for _ in range(times):
        if until is not None and until():
            return
        await anyio.lowlevel.checkpoint()

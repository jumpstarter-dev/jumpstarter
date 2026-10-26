import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .driver import (
    DEFAULT_RTP_MTU,
    AutoAcceptDelegate,
    BtPeer,
    BtPeerError,
    _check_sbc_frame,
    _first_sbc_frame,
    _Track,
)


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


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_bt_peer_default_transport():
    peer = BtPeer()
    assert peer.transport == "tcp-client:127.0.0.1:7300"


def test_bt_peer_custom_transport():
    peer = BtPeer(transport="usb:0")
    assert peer.transport == "usb:0"


def test_auto_accept_delegate_init():
    delegate = AutoAcceptDelegate()
    assert delegate is not None


@pytest.mark.anyio
async def test_auto_accept_delegate_confirms():
    delegate = AutoAcceptDelegate()
    assert await delegate.confirm() is True
    assert await delegate.compare_numbers(123456, 6) is True
    assert await delegate.accept() is True
    assert await delegate.get_number() == 0


def _make_mock_transport():
    transport = MagicMock()
    transport.source = MagicMock()
    transport.sink = MagicMock()
    transport.close = AsyncMock()
    return transport


def _make_mock_device(address="DA:4C:10:DE:00:01"):
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


@pytest.mark.anyio
async def test_start_peer_and_stop():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_avdtp = MagicMock()
        mock_listener_cls.for_device.return_value = mock_avdtp

        result = await peer.start_peer('{"name": "Test-Phone"}')
        parsed = json.loads(result)
        assert parsed["name"] == "Test-Phone"
        assert parsed["address"] == "DA:4C:10:DE:00:01"
        mock_device.power_on.assert_awaited_once()
        mock_device.set_discoverable.assert_awaited_once_with(True)
        mock_device.set_connectable.assert_awaited_once_with(True)

        with pytest.raises(BtPeerError, match="already running"):
            await peer.start_peer()

        result = await peer.stop_peer()
        parsed = json.loads(result)
        assert parsed["status"] == "stopped"
        mock_device.power_off.assert_awaited_once()
        mock_transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_peer_not_running():
    peer = BtPeer()
    result = await peer.stop_peer()
    parsed = json.loads(result)
    assert parsed["status"] == "not_running"


@pytest.mark.anyio
async def test_start_peer_rejects_non_object_config():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="JSON object"):
        await peer.start_peer("[]")
    with pytest.raises(BtPeerError, match="invalid config JSON"):
        await peer.start_peer("{")


@pytest.mark.anyio
async def test_stop_peer_cleans_up_when_power_off_fails():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()
    mock_device.power_off = AsyncMock(side_effect=RuntimeError("power off failed"))

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="failed to stop peer"):
            await peer.stop_peer()

    assert peer._device is None
    assert peer._transport is None
    mock_transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_default_config():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()

        result = await peer.start_peer()
        parsed = json.loads(result)
        assert parsed["name"] == "Bumble-Phone"

        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_custom_cod():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ) as device_cls,
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()

        result = await peer.start_peer('{"name": "Test", "class_of_device": 42}')
        parsed = json.loads(result)
        assert parsed["name"] == "Test"
        config = device_cls.call_args[1]["config"]
        assert config.class_of_device == 42

        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_classic_disabled():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()

        await peer.start_peer('{"classic_enabled": false}')
        mock_device.set_discoverable.assert_not_awaited()
        mock_device.set_connectable.assert_not_awaited()

        await peer.stop_peer()


@pytest.mark.anyio
async def test_get_address():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        peer.get_address()

    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device("AA:BB:CC:DD:EE:FF")

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        assert peer.get_address() == "AA:BB:CC:DD:EE:FF"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_get_events():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        events_json = peer.get_events("0")
        events = json.loads(events_json)
        assert isinstance(events, list)
        assert len(events) == 1
        assert events[0]["type"] == "peer_started"
        with pytest.raises(BtPeerError, match="invalid since value"):
            peer.get_events("abc")
        await peer.stop_peer()


@pytest.mark.anyio
async def test_get_connections_empty():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        conns = json.loads(peer.get_connections())
        assert conns == []
        await peer.stop_peer()


@pytest.mark.anyio
async def test_on_connection_tracking():
    peer = BtPeer()

    mock_conn = MagicMock()
    mock_conn.handle = 42
    mock_conn.peer_address = "11:22:33:44:55:66"
    mock_conn.transport = 0
    mock_conn.role = 1
    mock_conn.is_encrypted = False
    mock_conn.on = MagicMock()

    peer._on_connection(mock_conn)

    assert 42 in peer._connections
    conns = json.loads(peer.get_connections())
    assert len(conns) == 1
    assert conns[0]["handle"] == 42
    assert conns[0]["address"] == "11:22:33:44:55:66"


@pytest.mark.anyio
async def test_on_connection_disconnect():
    peer = BtPeer()

    mock_conn = MagicMock()
    mock_conn.handle = 7
    mock_conn.peer_address = "AA:BB:CC:DD:EE:FF"
    mock_conn.transport = 0
    mock_conn.role = 0
    mock_conn.is_encrypted = True
    mock_conn.on = MagicMock()

    peer._on_connection(mock_conn)
    assert 7 in peer._connections

    disconnect_handler = mock_conn.on.call_args[0][1]
    disconnect_handler(0x13)
    assert 7 not in peer._connections


@pytest.mark.anyio
async def test_wait_connection_not_running():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        await peer.wait_connection()


@pytest.mark.anyio
async def test_wait_connection_timeout():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="no connection within"):
            await peer.wait_connection(timeout=0)
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_not_running():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        await peer.wait_disconnection()


@pytest.mark.anyio
async def test_wait_disconnection_no_connections():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        result = json.loads(await peer.wait_disconnection())
        assert result["status"] == "no_connections"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_removes_handlers():
    peer = BtPeer()
    mock_conn = MagicMock()
    mock_conn.handle = 1
    mock_conn.peer_address = "11:22:33:44:55:66"
    mock_conn.transport = 0
    mock_conn.role = 1
    mock_conn.is_encrypted = False
    mock_conn.on = MagicMock()
    mock_conn.remove_listener = MagicMock()
    peer._connections[1] = mock_conn
    peer._device = MagicMock()

    with pytest.raises(BtPeerError, match="no disconnection within"):
        await peer.wait_disconnection(timeout=0)

    mock_conn.on.assert_called_once()
    assert mock_conn.on.call_args.args[0] == "disconnection"
    mock_conn.remove_listener.assert_called_once_with(
        "disconnection", mock_conn.on.call_args.args[1]
    )


@pytest.mark.anyio
async def test_pair_not_running():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        await peer.pair()


@pytest.mark.anyio
async def test_pair_no_connection():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="no connection with handle"):
            await peer.pair(99)
        await peer.stop_peer()


@pytest.mark.anyio
async def test_pair_success():
    peer = BtPeer()

    mock_conn = MagicMock()
    mock_conn.handle = 1
    mock_conn.peer_address = "11:22:33:44:55:66"
    mock_conn.transport = 0
    mock_conn.role = 1
    mock_conn.is_encrypted = True
    mock_conn.on = MagicMock()
    mock_conn.authenticate = AsyncMock()
    mock_conn.encrypt = AsyncMock()

    peer._on_connection(mock_conn)

    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        result = json.loads(await peer.pair(1))
        assert result["encrypted"] is True
        assert result["handle"] == 1
        mock_conn.authenticate.assert_awaited_once()
        mock_conn.encrypt.assert_awaited_once()
        await peer.stop_peer()


@pytest.mark.anyio
async def test_connect_to_not_running():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        await peer.connect_to("AA:BB:CC:DD:EE:FF")


@pytest.mark.anyio
async def test_connect_to():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()

    mock_outgoing = MagicMock()
    mock_outgoing.handle = 5
    mock_outgoing.peer_address = "AA:BB:CC:DD:EE:FF"
    mock_outgoing.transport = 0
    mock_outgoing.is_encrypted = False
    mock_device.connect = AsyncMock(return_value=mock_outgoing)

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        await peer.start_peer()
        result = json.loads(await peer.connect_to("AA:BB:CC:DD:EE:FF"))
        assert result["handle"] == 5
        assert result["role"] == "central"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_emit_events():
    peer = BtPeer()
    peer._emit("test_event", {"key": "value"})
    assert len(peer._events) == 1
    assert peer._events[0]["type"] == "test_event"
    assert peer._events[0]["key"] == "value"
    assert "ts" in peer._events[0]


@pytest.mark.anyio
async def test_on_avdtp_connection():
    peer = BtPeer()
    mock_protocol = MagicMock()
    mock_protocol.l2cap_channel.peer_mtu = 672

    peer._on_avdtp_connection(mock_protocol)

    assert peer._rtp_mtu == 672
    mock_protocol.add_source.assert_called_once()
    pump = mock_protocol.add_source.call_args.args[1]
    packets = pump.packets
    first = await packets.__anext__()
    assert hasattr(first, "timestamp_seconds")
    assert bytes(first)
    events = list(peer._events)
    assert any(e["type"] == "avdtp_connected" for e in events)


@pytest.mark.anyio
async def test_on_avdtp_connection_rejects_implausible_mtu():
    for peer_mtu in (None, "672", 4, MagicMock()):
        peer = BtPeer()
        mock_protocol = MagicMock()
        mock_protocol.l2cap_channel.peer_mtu = peer_mtu

        peer._on_avdtp_connection(mock_protocol)

        assert peer._rtp_mtu == DEFAULT_RTP_MTU
        # _emit serializes to JSON, so a non-int MTU would blow up there.
        assert any(e["type"] == "avdtp_connected" for e in peer._events)


@pytest.mark.anyio
async def test_start_peer_exception_cleanup():
    peer = BtPeer()
    mock_transport = _make_mock_transport()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            side_effect=RuntimeError("device init failed"),
        ),pytest.raises(RuntimeError, match="device init failed")
    ):
        await peer.start_peer()

    assert peer._device is None
    assert peer._transport is None
    mock_transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_exception_cleanup_after_device_init():
    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = _make_mock_device()
    mock_device.power_on = AsyncMock(side_effect=RuntimeError("power on failed"))

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=mock_transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            return_value=mock_device,
        ),
        patch("jumpstarter_driver_bt_peer.driver.Listener") as mock_listener_cls,
    ):
        mock_listener_cls.for_device.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="power on failed"):
            await peer.start_peer()

    assert peer._device is None
    assert peer._transport is None
    mock_device.power_off.assert_awaited_once()
    mock_transport.close.assert_awaited_once()


class _FakeResource:
    """Stands in for the client upload stream handed to play()."""

    def __init__(self, data: bytes, chunk: int = 4096):
        self._chunks = [data[i : i + chunk] for i in range(0, len(data), chunk)]

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        for chunk in self._chunks:
            yield chunk


def _running_peer() -> BtPeer:
    peer = BtPeer()
    peer._device = MagicMock()
    return peer


def _patch_resource(payload: bytes):
    """Patch Driver.resource so play() sees `payload` as the client upload."""

    @contextlib.asynccontextmanager
    async def _resource(self, handle, timeout=7200):
        yield _FakeResource(payload)

    return patch.object(BtPeer, "resource", _resource)


@pytest.mark.anyio
async def test_first_sbc_frame_parses_pinned_format():
    frame = await _first_sbc_frame(make_sbc_file(4))
    assert frame.sampling_frequency == 44100
    assert frame.channel_mode == 2
    assert frame.block_count == 16
    assert frame.subband_count == 8
    assert frame.allocation_method == 0
    _check_sbc_frame(frame)


@pytest.mark.anyio
async def test_first_sbc_frame_rejects_non_sbc():
    with pytest.raises(BtPeerError, match="not SBC audio"):
        await _first_sbc_frame(b"ID3\x04 this is not audio")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"sf_index": 3}, "48000 Hz"),
        ({"channel_mode": 0}, "channel mode"),
        ({"bitpool": 90}, "bitpool 90"),
    ],
)
async def test_check_sbc_frame_rejects_mismatches(kwargs, expected):
    frame = await _first_sbc_frame(make_sbc_file(4, **kwargs))
    with pytest.raises(BtPeerError, match=expected):
        _check_sbc_frame(frame)


@pytest.mark.anyio
async def test_play_requires_running_peer():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="peer not running"):
        await peer.play("handle")


@pytest.mark.anyio
async def test_play_rejects_empty_upload():
    peer = _running_peer()
    with _patch_resource(b""), pytest.raises(BtPeerError, match="empty"):
        await peer.play("handle")


@pytest.mark.anyio
async def test_play_rejects_mismatched_sbc():
    peer = _running_peer()
    with _patch_resource(make_sbc_file(4, sf_index=3)), pytest.raises(BtPeerError, match="48000 Hz"):
        await peer.play("handle")
    assert peer._track is None


@pytest.mark.anyio
async def test_play_sets_track_and_reports_status():
    peer = _running_peer()
    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", loop=True))

    assert result["name"] == "song.mp3"
    assert result["loop"] is True
    assert result["bitpool"] == 52
    assert peer._track is not None

    status = json.loads(peer.playback_status())
    assert status == {
        "playing": True,
        "name": "song.mp3",
        "loop": True,
        "bytes": result["bytes"],
        "position": 0,
    }

    assert json.loads(peer.stop_playback())["status"] == "stopped"
    assert peer._track is None
    assert json.loads(peer.playback_status()) == {"playing": False}
    assert json.loads(peer.stop_playback())["status"] == "not_playing"


@pytest.mark.anyio
async def test_stop_peer_clears_playback():
    peer = _running_peer()
    with _patch_resource(make_sbc_file(10)):
        await peer.play("handle")
    peer._device = AsyncMock()
    peer._transport = _make_mock_transport()
    peer._rtp_mtu = 895

    await peer.stop_peer()

    assert peer._track is None
    assert peer._rtp_mtu == DEFAULT_RTP_MTU


@pytest.mark.anyio
async def test_media_source_timestamps_never_go_backwards():
    """MediaPacketPump paces off timestamps, so a track swap must not rewind them."""
    peer = BtPeer()
    source = peer._media_source()
    timestamps = []

    for _ in range(3):
        timestamps.append((await source.__anext__()).timestamp_seconds)

    peer._track = _Track(name="song.sbc", data=make_sbc_file(200), mtu=672)
    while peer._track is not None:
        timestamps.append((await source.__anext__()).timestamp_seconds)

    # And back to silence once the track drains.
    timestamps.append((await source.__anext__()).timestamp_seconds)

    assert timestamps == sorted(timestamps)
    assert timestamps[-1] > timestamps[2]
    assert any(e["type"] == "track_finished" for e in peer._events)


@pytest.mark.anyio
async def test_media_source_loops_without_rewinding_timestamps():
    peer = BtPeer()
    source = peer._media_source()
    peer._track = _Track(name="loop.sbc", data=make_sbc_file(30), mtu=672, loop=True)

    timestamps = [(await source.__anext__()).timestamp_seconds for _ in range(120)]

    assert timestamps == sorted(timestamps)
    assert peer._track is not None, "loop mode should keep the track active"
    assert any(e["type"] == "track_looped" for e in peer._events)


@pytest.mark.anyio
async def test_track_restart_rewinds_data():
    track = _Track(name="t", data=make_sbc_file(10), mtu=672)
    await track._reader.read(50)
    assert track.position == 50
    track.restart()
    assert track.position == 0
    assert track.last_timestamp == 0.0

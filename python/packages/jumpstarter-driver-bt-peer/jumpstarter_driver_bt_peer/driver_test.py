import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import anyio.lowlevel
import pytest
from bumble.avdtp import State
from bumble.l2cap import ClassicChannel

from .driver import (
    DEFAULT_RTP_MTU,
    AutoAcceptDelegate,
    AvrcpProtocol,
    BtPeer,
    BtPeerError,
    PduId,
    TrackMetadata,
    _A2dpSession,
    _AvrcpDelegate,
    _AvrcpProtocol,
    _parse_metadata,
    _Track,
    _validate_sbc,
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
async def test_on_avdtp_connection_creates_a_session():
    peer = BtPeer()
    protocol = _fake_protocol()

    peer._on_avdtp_connection(protocol)

    assert peer._session is not None
    protocol.add_source.assert_called_once()
    assert any(e["type"] == "avdtp_connected" for e in peer._events)


@pytest.mark.anyio
async def test_avdtp_close_drops_the_session():
    """A session outliving its channel leaves play() on a dead protocol."""
    peer = BtPeer()
    protocol = _fake_protocol()

    peer._on_avdtp_connection(protocol)
    assert peer._session is not None

    # Fire whatever handler was registered for the channel's close event.
    event, handler = protocol.l2cap_channel.on.call_args.args
    assert event == ClassicChannel.EVENT_CLOSE
    handler()

    assert peer._session is None
    assert any(e["type"] == "avdtp_disconnected" for e in peer._events)
    # And a play() afterwards must not try to use the dead protocol.
    assert await peer._ensure_streaming() == {
        "streaming": False,
        "reason": "no AVDTP connection from the DUT yet",
    }


@pytest.mark.anyio
async def test_rtp_mtu_comes_from_the_media_channel_not_signalling():
    """The signalling channel's MTU does not bound RTP payloads.

    The media transport channel is a separate L2CAP channel created when the
    stream opens, and only its MTU may size a packet.
    """
    peer = BtPeer()
    protocol = _fake_protocol(signalling_mtu=1024)
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
    protocol = _fake_protocol()
    peer._on_avdtp_connection(protocol)
    session = peer._session
    assert session is not None

    pump = protocol.add_source.call_args.args[1]
    await pump.start(MagicMock(peer_mtu=bad_mtu))

    assert session.mtu == DEFAULT_RTP_MTU
    # _emit serializes to JSON, so a non-int MTU would blow up there.
    assert any(e["type"] == "rtp_channel_open" for e in peer._events)


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
async def test_validate_sbc_parses_pinned_format():
    frame = await _validate_sbc(make_sbc_file(4))
    assert frame.sampling_frequency == 44100
    assert frame.channel_mode == 2
    assert frame.block_count == 16
    assert frame.subband_count == 8
    assert frame.allocation_method == 0


@pytest.mark.anyio
async def test_validate_sbc_rejects_non_sbc():
    with pytest.raises(BtPeerError, match="not SBC audio"):
        await _validate_sbc(b"ID3\x04 this is not audio")


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
        await _validate_sbc(make_sbc_file(4, **kwargs))


@pytest.mark.anyio
async def test_validate_sbc_checks_every_frame_not_just_the_first():
    """SbcPacketSource concatenates later frames without re-checking them."""
    good = make_sbc_file(4)
    mixed = good + make_sbc_file(1, sf_index=3) + good

    with pytest.raises(BtPeerError, match="48000 Hz") as excinfo:
        await _validate_sbc(mixed)

    assert "frame 4" in str(excinfo.value), excinfo.value


@pytest.mark.anyio
async def test_validate_sbc_honours_the_negotiated_bitpool_ceiling():
    """A sink may accept less than the advertised maximum."""
    data = make_sbc_file(4, bitpool=52)

    await _validate_sbc(data, max_bitpool=53)

    with pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await _validate_sbc(data, max_bitpool=35)


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
    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", loop=True))

    assert result["name"] == "song.mp3"
    assert result["loop"] is True
    assert result["bitpool"] == 52
    assert peer._track is not None

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    status = json.loads(peer.playback_status())
    assert status == {
        "playing": True,
        "name": "song.mp3",
        "loop": True,
        "bytes": result["bytes"],
        "position": 0,
        "streaming": True,
        # No tags were supplied, so the title falls back to the file name.
        "title": "song.mp3",
        "artist": "",
        "album": "",
        "duration_ms": 0,
        "position_ms": 0,
    }

    assert json.loads(await peer.stop_playback())["status"] == "stopped"
    assert peer._track is None
    protocol.stream.state = State.OPEN
    assert json.loads(peer.playback_status()) == {"playing": False, "streaming": False}
    assert json.loads(await peer.stop_playback())["status"] == "not_playing"


@pytest.mark.anyio
async def test_stop_peer_clears_playback():
    peer = _running_peer()
    with _patch_resource(make_sbc_file(10)):
        await peer.play("handle")
    peer._device = AsyncMock()
    peer._transport = _make_mock_transport()
    peer._session = MagicMock()

    await peer.stop_peer()

    assert peer._track is None
    assert peer._session is None


def _session_for(peer):
    """A session bound to `peer`, without needing a real AVDTP protocol."""
    return _A2dpSession(
        _fake_protocol(),
        get_track=lambda: peer._track,
        on_exhausted=peer._on_track_exhausted,
        emit=peer._emit,
    )


@pytest.mark.anyio
async def test_media_source_timestamps_never_go_backwards():
    """MediaPacketPump paces off timestamps, so a track swap must not rewind them."""
    peer = BtPeer()
    source = _session_for(peer)._media_source()
    timestamps = []

    for _ in range(3):
        timestamps.append((await source.__anext__()).timestamp_seconds)

    peer._track = _Track(name="song.sbc", data=make_sbc_file(200))
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
    source = _session_for(peer)._media_source()
    peer._track = _Track(name="loop.sbc", data=make_sbc_file(30), loop=True)

    timestamps = [(await source.__anext__()).timestamp_seconds for _ in range(120)]

    assert timestamps == sorted(timestamps)
    assert peer._track is not None, "loop mode should keep the track active"
    assert any(e["type"] == "track_looped" for e in peer._events)


@pytest.mark.anyio
async def test_track_rewind_resets_data_and_generator():
    track = _Track(name="t", data=make_sbc_file(10))
    first = track.packets(672)
    await track._reader.read(50)
    assert track.position == 50

    track.rewind()

    assert track.position == 0
    assert track.last_timestamp == 0.0
    assert track.packets(672) is not first, "rewind must build a fresh generator"


def test_track_packet_generator_is_held_not_rebuilt():
    """SbcPacketSource.packets is a property that yields a new generator."""
    track = _Track(name="t", data=make_sbc_file(10))
    assert track.packets(672) is track.packets(672)


def _sink_with_bitpool(minimum: int, maximum: int):
    from bumble.a2dp import SbcMediaCodecInformation
    from bumble.avdtp import MediaCodecCapabilities

    info = MagicMock(spec=SbcMediaCodecInformation)
    info.minimum_bitpool_value = minimum
    info.maximum_bitpool_value = maximum
    capability = MagicMock(spec=MediaCodecCapabilities)
    capability.media_codec_information = info
    sink = MagicMock()
    sink.capabilities = [capability]
    return sink


def _fake_protocol(signalling_mtu: int = 1024, sink=None, stream_state=State.CONFIGURED):
    """An AVDTP protocol whose add_source records the pump the session builds.

    signalling_mtu is deliberately distinct from any media channel MTU a test
    supplies, so a test fails if the wrong channel is read.
    """
    source = MagicMock()
    source.stream = None
    source.capabilities = _sink_with_bitpool(0, 0).capabilities

    stream = AsyncMock()
    stream.state = stream_state

    protocol = MagicMock()
    protocol.l2cap_channel.peer_mtu = signalling_mtu
    protocol.add_source = MagicMock(return_value=source)
    protocol.discover_remote_endpoints = AsyncMock()
    protocol.find_remote_sink_by_codec = MagicMock(
        return_value=sink if sink is not None else _sink_with_bitpool(2, 53)
    )
    protocol.create_stream = AsyncMock(return_value=stream)
    protocol.stream = stream
    protocol.source = source
    return protocol


def _peer_with_session(**kwargs):
    """A running peer with an AVDTP session already established."""
    peer = _running_peer()
    peer._avrcp_delegate = _AvrcpDelegate()
    peer._avrcp = MagicMock()
    protocol = _fake_protocol(**kwargs)
    peer._on_avdtp_connection(protocol)
    return peer, protocol


@pytest.mark.anyio
async def test_ensure_streaming_without_avdtp_connection():
    peer = BtPeer()
    assert await peer._ensure_streaming() == {
        "streaming": False,
        "reason": "no AVDTP connection from the DUT yet",
    }


@pytest.mark.anyio
async def test_ensure_streaming_is_a_noop_when_already_streaming():
    peer, protocol = _peer_with_session()
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING

    result = await peer._ensure_streaming()

    assert result == {"streaming": True, "reason": "already streaming"}
    protocol.discover_remote_endpoints.assert_not_called()
    protocol.stream.start.assert_not_awaited()


@pytest.mark.anyio
async def test_ensure_streaming_initiates_the_stream():
    """The DUT opens AVDTP signalling and waits; the source has to start it."""
    peer, protocol = _peer_with_session(sink=_sink_with_bitpool(2, 35))

    result = await peer._ensure_streaming()

    assert result == {"streaming": True, "reason": "started by the peer"}
    protocol.discover_remote_endpoints.assert_awaited_once()
    protocol.create_stream.assert_awaited_once()
    protocol.stream.start.assert_awaited_once()
    # The offered bitpool range must be clamped to what the sink accepts.
    offered = protocol.source.capabilities[0].media_codec_information
    assert offered.minimum_bitpool_value == 2
    assert offered.maximum_bitpool_value == 35
    assert any(e["type"] == "sink_bitpool" for e in peer._events)


@pytest.mark.anyio
async def test_a_stream_left_idle_by_a_close_is_recreated():
    """start() on an IDLE stream raises; it has to be configured afresh."""
    peer, protocol = _peer_with_session()
    stale = AsyncMock()
    stale.state = State.IDLE
    protocol.source.stream = stale

    result = await peer._ensure_streaming()

    assert result == {"streaming": True, "reason": "started by the peer"}
    stale.start.assert_not_awaited()
    protocol.discover_remote_endpoints.assert_awaited_once()
    protocol.create_stream.assert_awaited_once()
    protocol.stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_an_open_stream_is_reused_rather_than_recreated():
    peer, protocol = _peer_with_session()
    open_stream = AsyncMock()
    open_stream.state = State.OPEN
    protocol.source.stream = open_stream

    result = await peer._ensure_streaming()

    assert result == {"streaming": True, "reason": "started by the peer"}
    protocol.discover_remote_endpoints.assert_not_called()
    open_stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_ensure_streaming_without_a_sink_endpoint():
    peer, _protocol = _peer_with_session(sink=None)
    peer._session.protocol.find_remote_sink_by_codec = MagicMock(return_value=None)

    with pytest.raises(BtPeerError, match="no free SBC audio sink"):
        await peer._ensure_streaming()


@pytest.mark.anyio
async def test_play_reports_that_it_started_the_stream():
    peer, _protocol = _peer_with_session()

    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3"))

    assert result["streaming"] is True
    assert result["reason"] == "started by the peer"


@pytest.mark.anyio
async def test_start_peer_advertises_avrcp_target():
    """Without UUID 110C the DUT leaves its A2DP sink inactive and drops the audio."""
    from bumble.sdp import SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID

    peer = BtPeer()
    mock_transport = _make_mock_transport()
    mock_device = AsyncMock()
    mock_device.public_address = "AA:BB:CC:DD:EE:FF"
    mock_device.on = MagicMock()  # sync in bumble; AsyncMock would leave a coroutine unawaited

    with (
        patch("jumpstarter_driver_bt_peer.driver.open_transport", new=AsyncMock(return_value=mock_transport)),
        patch("jumpstarter_driver_bt_peer.driver.Device", return_value=mock_device),
        patch("jumpstarter_driver_bt_peer.driver.Listener"),
        patch("jumpstarter_driver_bt_peer.driver._AvrcpProtocol") as avrcp_protocol,
    ):
        await peer.start_peer("{}")

    uuids = []
    for record in mock_device.sdp_service_records.values():
        for attribute in record:
            if attribute.id == SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID:
                uuids += [str(v.value) for v in attribute.value.value]

    assert any("110A" in u for u in uuids), f"missing A2DP AudioSource: {uuids}"
    assert any("110C" in u for u in uuids), f"missing AVRCP Target: {uuids}"
    avrcp_protocol.return_value.listen.assert_called_once_with(mock_device)


@pytest.mark.anyio
async def test_play_reports_playing_over_avrcp():
    from bumble.avrcp import PlayStatus

    peer, _protocol = _peer_with_session()

    with _patch_resource(make_sbc_file(100)):
        await peer.play("handle", name="song.mp3")

    assert peer._avrcp_delegate.playback_status == PlayStatus.PLAYING
    peer._avrcp.notify_playback_status_changed.assert_called_with(PlayStatus.PLAYING)

    await peer.stop_playback()
    assert peer._avrcp_delegate.playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_play_does_not_claim_playing_without_a_stream():
    """A queued track is not playing yet; saying so would mislead the DUT."""
    from bumble.avrcp import PlayStatus

    peer = _running_peer()
    peer._avrcp_delegate = _AvrcpDelegate()
    peer._avrcp = MagicMock()

    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3"))

    assert result["streaming"] is False
    assert peer._avrcp_delegate.playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_avrcp_notification_failure_does_not_break_play():
    """A controller that never registered must not fail the play call."""
    peer = _running_peer()
    peer._avrcp_delegate = _AvrcpDelegate()
    peer._avrcp = MagicMock()
    peer._avrcp.notify_playback_status_changed.side_effect = RuntimeError("no listener")

    with _patch_resource(make_sbc_file(10)):
        result = json.loads(await peer.play("handle"))

    assert result["bytes"] > 0


@pytest.mark.anyio
async def test_avrcp_transport_keys_are_ignored_by_default():
    """A DUT that sends PAUSE on connect must not silence an explicit play()."""
    from bumble.avrcp import PlayStatus

    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")
    protocol.stream.stop.reset_mock()

    pause = MagicMock()
    pause.name = "PAUSE"
    peer._on_avrcp_key(pause, pressed=True)
    for _ in range(20):
        await anyio.lowlevel.checkpoint()

    protocol.stream.stop.assert_not_awaited()
    assert peer._avrcp_delegate.playback_status == PlayStatus.PLAYING
    event = [e for e in peer._events if e["type"] == "avrcp_key"][-1]
    assert event == {**event, "key": "PAUSE", "pressed": True, "honored": False}


@pytest.mark.anyio
async def test_avrcp_transport_keys_suspend_and_restart_the_stream():
    """Pause is an AVDTP SUSPEND, not filler audio; play restarts the stream."""
    from bumble.avrcp import PlayStatus

    peer, protocol = _peer_with_session()
    peer.follow_transport_keys = True
    with _patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    protocol.stream.start.reset_mock()

    pause = MagicMock()
    pause.name = "PAUSE"
    peer._on_avrcp_key(pause, pressed=True)
    for _ in range(20):
        await anyio.lowlevel.checkpoint()

    protocol.stream.stop.assert_awaited_once()
    assert peer._avrcp_delegate.playback_status == PlayStatus.PAUSED
    assert peer._track is not None, "pause keeps the track"

    protocol.stream.state = State.OPEN
    play = MagicMock()
    play.name = "PLAY"
    peer._on_avrcp_key(play, pressed=True)
    for _ in range(20):
        await anyio.lowlevel.checkpoint()

    protocol.stream.start.assert_awaited_once()
    assert peer._avrcp_delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_pause_keeps_position_and_resume_continues():
    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(200)):
        await peer.play("handle", name="song.mp3")

    source = peer._session._media_source()
    for _ in range(10):
        await source.__anext__()
    position = peer._track.position
    assert position > 0

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    await peer.pause()

    protocol.stream.stop.assert_awaited_once()
    assert peer._track.position == position, "pause must not rewind the track"

    protocol.stream.state = State.OPEN
    result = json.loads(await peer.resume())
    assert result["status"] == "resumed"
    assert peer._track.position == position


@pytest.mark.anyio
async def test_stop_playback_suspends_and_clears_the_track():
    from bumble.avrcp import PlayStatus

    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING

    result = json.loads(await peer.stop_playback())

    assert result == {"status": "stopped", "name": "song.mp3"}
    protocol.stream.stop.assert_awaited_once()
    assert peer._track is None
    assert peer._avrcp_delegate.playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_pause_and_resume_without_a_track():
    peer, _protocol = _peer_with_session()
    assert json.loads(await peer.pause())["status"] == "not_playing"
    assert json.loads(await peer.resume())["status"] == "not_playing"


@pytest.mark.anyio
async def test_track_queued_before_avdtp_starts_when_it_connects():
    """play() can land between the DUT's ACL connection and its AVDTP channel.

    It then has no protocol to start a stream on. The track must not sit there
    silently until something calls play again.
    """
    from bumble.avrcp import PlayStatus

    peer = _running_peer()
    peer._avrcp_delegate = _AvrcpDelegate()
    peer._avrcp = MagicMock()

    # play() before any AVDTP connection: nothing to stream on.
    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3"))
    assert result["streaming"] is False
    assert peer._track is not None

    # The DUT's AVDTP channel arrives afterwards.
    protocol = _fake_protocol()
    peer._on_avdtp_connection(protocol)
    # _on_avdtp_connection is a sync callback, so the resume runs as a task.
    for _ in range(50):
        if protocol.stream.start.await_count:
            break
        await anyio.lowlevel.checkpoint()

    protocol.stream.start.assert_awaited_once()
    assert any(e["type"] == "stream_resumed" for e in peer._events)
    assert peer._avrcp_delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_avdtp_connection_without_a_queued_track_starts_nothing():
    peer = BtPeer()
    protocol = _fake_protocol()

    peer._on_avdtp_connection(protocol)
    for _ in range(10):
        await anyio.lowlevel.checkpoint()

    protocol.discover_remote_endpoints.assert_not_awaited()
    assert not any(e["type"] == "stream_resumed" for e in peer._events)


@pytest.mark.anyio
async def test_resume_failure_is_reported_not_raised():
    peer, protocol = _peer_with_session()
    peer._track = _Track(name="t", data=make_sbc_file(10))
    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)

    await peer._resume_queued_track()

    failed = [e for e in peer._events if e["type"] == "stream_resume_failed"]
    assert failed and "no free SBC audio sink" in failed[0]["error"]


@pytest.mark.anyio
async def test_failed_play_does_not_leave_a_track_installed():
    """A track left behind would make playback_status claim to be playing."""
    from bumble.avrcp import PlayStatus

    peer, protocol = _peer_with_session()
    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)

    with _patch_resource(make_sbc_file(50)), pytest.raises(BtPeerError, match="no free SBC audio sink"):
        await peer.play("handle", name="song.mp3")

    assert peer._track is None
    assert json.loads(peer.playback_status())["playing"] is False
    assert peer._avrcp_delegate.playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_failed_play_restores_the_previous_track():
    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(50)):
        await peer.play("handle", name="first.mp3")

    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)
    protocol.source.stream = None
    with _patch_resource(make_sbc_file(50)), pytest.raises(BtPeerError):
        await peer.play("handle", name="second.mp3")

    assert peer._track is not None
    assert peer._track.name == "first.mp3"


@pytest.mark.anyio
async def test_play_rejects_audio_above_the_negotiated_bitpool():
    """The sink's ceiling can be below the range the peer advertises."""
    peer, _protocol = _peer_with_session(sink=_sink_with_bitpool(2, 35))

    with _patch_resource(make_sbc_file(50, bitpool=52)), pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await peer.play("handle", name="loud.sbc")

    assert peer._track is None


@pytest.mark.anyio
async def test_resume_discards_a_track_stopped_while_starting():
    """stop_playback() can land while endpoint discovery is still in flight."""
    from bumble.avrcp import PlayStatus

    peer, protocol = _peer_with_session()
    peer._track = _Track(name="t", data=make_sbc_file(10))

    async def stop_midway():
        peer._track = None

    protocol.discover_remote_endpoints = AsyncMock(side_effect=stop_midway)

    await peer._resume_queued_track()

    assert any(e["type"] == "stream_resume_discarded" for e in peer._events)
    assert not any(e["type"] == "stream_resumed" for e in peer._events)
    assert peer._avrcp_delegate.playback_status != PlayStatus.PLAYING


@pytest.mark.anyio
async def test_a_rejected_upload_leaves_the_current_track_playing():
    """Validating after suspending would silence audio over a bad upload."""
    peer, protocol = _peer_with_session()
    with _patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="good.sbc")

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    protocol.stream.stop.reset_mock()

    bad = make_sbc_file(4) + make_sbc_file(1, sf_index=3)
    with _patch_resource(bad), pytest.raises(BtPeerError, match="48000 Hz"):
        await peer.play("handle", name="bad.sbc")

    protocol.stream.stop.assert_not_awaited()
    assert peer._track is not None
    assert peer._track.name == "good.sbc"


def test_track_metadata_renders_requested_attributes():
    from bumble.avrcp import MediaAttributeId

    metadata = TrackMetadata(title="Fun Fun Music", artist="gr0za", album="Test", duration_ms=128040)
    attributes = metadata.as_media_attributes([MediaAttributeId.TITLE, MediaAttributeId.ARTIST_NAME])

    assert [(a.attribute_id, a.attribute_value) for a in attributes] == [
        (MediaAttributeId.TITLE, "Fun Fun Music"),
        (MediaAttributeId.ARTIST_NAME, "gr0za"),
    ]


def test_track_metadata_empty_request_means_everything():
    from bumble.avrcp import MediaAttributeId

    metadata = TrackMetadata(title="T", artist="A", album="B", duration_ms=1000)
    ids = [a.attribute_id for a in metadata.as_media_attributes([])]

    assert set(ids) == {
        MediaAttributeId.TITLE,
        MediaAttributeId.ARTIST_NAME,
        MediaAttributeId.ALBUM_NAME,
        MediaAttributeId.PLAYING_TIME,
    }


def test_track_metadata_omits_unset_fields():
    """An attribute with an empty value is left out rather than sent blank."""
    from bumble.avrcp import MediaAttributeId

    attributes = TrackMetadata(title="Only A Title").as_media_attributes([])

    assert [a.attribute_id for a in attributes] == [MediaAttributeId.TITLE]


def test_media_attributes_survive_bumble_encoding():
    """The response has to serialize; a bad character set id fails only here."""
    from bumble.avrcp import GetElementAttributesResponse

    metadata = TrackMetadata(title="Fun Fun Music", artist="gr0za", album="Test", duration_ms=128040)
    response = GetElementAttributesResponse(attributes=metadata.as_media_attributes([]))

    encoded = bytes(response)
    assert b"Fun Fun Music" in encoded
    assert b"gr0za" in encoded


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"title": "T", "artist": "A", "album": "B", "duration_ms": 1500}', ("T", "A", "B", 1500)),
        ("{}", ("fallback.mp3", "", "", 0)),
        ('{"title": ""}', ("fallback.mp3", "", "", 0)),
        ('{"duration_ms": "2000"}', ("fallback.mp3", "", "", 2000)),
        ('{"duration_ms": "not a number"}', ("fallback.mp3", "", "", 0)),
        ('{"duration_ms": -5}', ("fallback.mp3", "", "", 0)),
    ],
)
def test_parse_metadata(raw, expected):
    metadata = _parse_metadata(raw, default_title="fallback.mp3")
    assert (metadata.title, metadata.artist, metadata.album, metadata.duration_ms) == expected


def test_parse_metadata_rejects_malformed_json():
    with pytest.raises(BtPeerError, match="invalid metadata JSON"):
        _parse_metadata("{not json", default_title="x")
    with pytest.raises(BtPeerError, match="must be a JSON object"):
        _parse_metadata("[1, 2]", default_title="x")


@pytest.mark.anyio
async def test_play_publishes_metadata_over_avrcp():
    from bumble.avrcp import MediaAttributeId

    peer = _running_peer()
    peer._avrcp_delegate = _AvrcpDelegate(position_provider=peer._current_position_ms)
    peer._avrcp = MagicMock()

    metadata = json.dumps({
        "title": "Fun Fun Music",
        "artist": "gr0za",
        "album": "Jumpstarter bt_peer test",
        "duration_ms": 128040,
    })
    with _patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", metadata_json=metadata))

    assert result["title"] == "Fun Fun Music"
    assert result["artist"] == "gr0za"
    assert result["duration_ms"] == 128040

    attributes = await peer._avrcp_delegate.get_media_attributes([MediaAttributeId.TITLE])
    assert attributes[0].attribute_value == "Fun Fun Music"

    length, position = await peer._avrcp_delegate.get_song_length_and_position()
    assert length == 128040
    assert position == 0

    peer._avrcp.notify_track_changed.assert_called_once()


@pytest.mark.anyio
async def test_play_status_is_unavailable_without_a_duration():
    """A file with no duration must not claim a length of zero."""
    from bumble.avrcp import GetPlayStatusResponse

    delegate = _AvrcpDelegate()
    length, position = await delegate.get_song_length_and_position()

    assert length == GetPlayStatusResponse.UNAVAILABLE
    assert position == GetPlayStatusResponse.UNAVAILABLE


@pytest.mark.anyio
async def test_position_tracks_bytes_consumed():
    peer, _protocol = _peer_with_session()
    peer._avrcp_delegate = _AvrcpDelegate(position_provider=peer._current_position_ms)

    with _patch_resource(make_sbc_file(200)):
        await peer.play("handle", metadata_json=json.dumps({"duration_ms": 10000}))

    assert peer._current_position_ms() == 0

    source = peer._session._media_source()
    for _ in range(20):
        await source.__anext__()

    position = peer._current_position_ms()
    assert 0 < position < 10000, position


@pytest.mark.anyio
async def test_get_element_attributes_is_dispatched():
    """bumble rejects this PDU by default; the subclass has to answer it."""
    from bumble.avrcp import GetElementAttributesCommand, MediaAttributeId, PduId

    protocol = _AvrcpProtocol.__new__(_AvrcpProtocol)
    protocol.delegate = _AvrcpDelegate()
    protocol.delegate.metadata = TrackMetadata(title="Fun Fun Music", artist="gr0za")
    protocol.receive_command_state = MagicMock(transaction_label=7)
    command = GetElementAttributesCommand(identifier=0, attribute_ids=[MediaAttributeId.TITLE])

    with (
        patch.object(_AvrcpProtocol, "send_avrcp_response") as send,
        patch.object(_AvrcpProtocol, "_delegate_command") as delegate_command,
    ):
        protocol._on_command_pdu(PduId.GET_ELEMENT_ATTRIBUTES, bytes(command))
        # Await the coroutine the dispatcher would have spawned.
        await delegate_command.call_args.args[2]

    assert protocol.receive_command_state is None
    label, _code, response = send.call_args.args
    assert label == 7
    assert [a.attribute_value for a in response.attributes] == ["Fun Fun Music"]


@pytest.mark.anyio
async def test_other_pdus_still_reach_the_base_class():
    protocol = _AvrcpProtocol.__new__(_AvrcpProtocol)
    protocol.receive_command_state = MagicMock(transaction_label=1)

    with patch.object(AvrcpProtocol, "_on_command_pdu") as base:
        protocol._on_command_pdu(PduId.GET_PLAY_STATUS, b"")

    base.assert_called_once()

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bumble.avdtp import State
from bumble.avrcp import PlayStatus
from bumble.l2cap import ClassicChannel

from .a2dp import Track
from .avrcp import PeerDelegate, TrackMetadata
from .conftest import (
    avrcp_mock,
    delegate_of,
    fake_protocol,
    make_device,
    make_sbc_file,
    make_transport,
    patch_resource,
    patched_stack,
    peer_with_session,
    running_peer,
    settle,
    sink_with_bitpool,
)
from .driver import AutoAcceptDelegate, BtPeer, BtPeerError
from .player import Idle, Playing

# -- configuration -------------------------------------------------------


def test_bt_peer_default_transport():
    assert BtPeer().transport == "tcp-client:127.0.0.1:7300"


def test_bt_peer_custom_transport():
    assert BtPeer(transport="usb:0").transport == "usb:0"


def test_auto_accept_delegate_init():
    assert AutoAcceptDelegate() is not None


@pytest.mark.anyio
async def test_auto_accept_delegate_confirms():
    delegate = AutoAcceptDelegate()
    assert await delegate.confirm() is True
    assert await delegate.compare_numbers(123456, 6) is True
    assert await delegate.accept() is True
    assert await delegate.get_number() == 0


# -- device lifecycle ----------------------------------------------------


@pytest.mark.anyio
async def test_start_peer_and_stop():
    peer = BtPeer()
    transport = make_transport()
    device = make_device()

    with patched_stack(device=device, transport=transport):
        parsed = json.loads(await peer.start_peer('{"name": "Test-Phone"}'))
        assert parsed["name"] == "Test-Phone"
        assert parsed["address"] == "DA:4C:10:DE:00:01"
        device.power_on.assert_awaited_once()
        device.set_discoverable.assert_awaited_once_with(True)
        device.set_connectable.assert_awaited_once_with(True)

        with pytest.raises(BtPeerError, match="already running"):
            await peer.start_peer()

        assert json.loads(await peer.stop_peer())["status"] == "stopped"
        device.power_off.assert_awaited_once()
        transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_stop_peer_not_running():
    assert json.loads(await BtPeer().stop_peer())["status"] == "not_running"


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
    transport = make_transport()
    device = make_device()
    device.power_off = AsyncMock(side_effect=RuntimeError("power off failed"))

    with patched_stack(device=device, transport=transport):
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="failed to stop peer"):
            await peer.stop_peer()

    assert peer._device is None
    assert peer._transport is None
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_default_config():
    peer = BtPeer()
    with patched_stack():
        assert json.loads(await peer.start_peer())["name"] == "Bumble-Phone"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_custom_cod():
    peer = BtPeer()
    with patched_stack() as device_cls:
        parsed = json.loads(await peer.start_peer('{"name": "Test", "class_of_device": 42}'))
        assert parsed["name"] == "Test"
        assert device_cls.call_args[1]["config"].class_of_device == 42
        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_classic_disabled():
    peer = BtPeer()
    device = make_device()
    with patched_stack(device=device):
        await peer.start_peer('{"classic_enabled": false}')
        device.set_discoverable.assert_not_awaited()
        device.set_connectable.assert_not_awaited()
        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_exception_cleanup():
    peer = BtPeer()
    transport = make_transport()

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=transport),
        ),
        patch(
            "jumpstarter_driver_bt_peer.driver.Device",
            side_effect=RuntimeError("device init failed"),
        ),
        pytest.raises(RuntimeError, match="device init failed"),
    ):
        await peer.start_peer()

    assert peer._device is None
    assert peer._transport is None
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_exception_cleanup_after_device_init():
    peer = BtPeer()
    transport = make_transport()
    device = make_device()
    device.power_on = AsyncMock(side_effect=RuntimeError("power on failed"))

    with patched_stack(device=device, transport=transport), pytest.raises(RuntimeError, match="power on failed"):
        await peer.start_peer()

    assert peer._device is None
    assert peer._transport is None
    assert peer._reporter is None, "a half-built peer must not keep an AVRCP target"
    device.power_off.assert_awaited_once()
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_advertises_avrcp_target():
    """Without UUID 110C the DUT leaves its A2DP sink inactive and drops the audio."""
    from bumble.sdp import SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID

    peer = BtPeer()
    device = AsyncMock()
    device.public_address = "AA:BB:CC:DD:EE:FF"
    device.on = MagicMock()  # sync in bumble; AsyncMock would leave a coroutine unawaited

    with (
        patch(
            "jumpstarter_driver_bt_peer.driver.open_transport",
            new=AsyncMock(return_value=make_transport()),
        ),
        patch("jumpstarter_driver_bt_peer.driver.Device", return_value=device),
        patch("jumpstarter_driver_bt_peer.driver.Listener"),
        patch("jumpstarter_driver_bt_peer.driver.PeerProtocol") as avrcp_protocol,
    ):
        await peer.start_peer("{}")

    uuids = []
    for record in device.sdp_service_records.values():
        for attribute in record:
            if attribute.id == SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID:
                uuids += [str(v.value) for v in attribute.value.value]

    assert any("110A" in u for u in uuids), f"missing A2DP AudioSource: {uuids}"
    assert any("110C" in u for u in uuids), f"missing AVRCP Target: {uuids}"
    avrcp_protocol.return_value.listen.assert_called_once_with(device)


# -- connections ---------------------------------------------------------


@pytest.mark.anyio
async def test_get_address():
    peer = BtPeer()
    with pytest.raises(BtPeerError, match="not running"):
        peer.get_address()

    with patched_stack(device=make_device("AA:BB:CC:DD:EE:FF")):
        await peer.start_peer()
        assert peer.get_address() == "AA:BB:CC:DD:EE:FF"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_get_events():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        events = json.loads(peer.get_events("0"))
        assert [e["type"] for e in events] == ["peer_started"]
        with pytest.raises(BtPeerError, match="invalid since value"):
            peer.get_events("abc")
        await peer.stop_peer()


@pytest.mark.anyio
async def test_get_connections_empty():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        assert json.loads(peer.get_connections()) == []
        await peer.stop_peer()


def _connection(handle=1, address="11:22:33:44:55:66", role=1, encrypted=False):
    conn = MagicMock()
    conn.handle = handle
    conn.peer_address = address
    conn.transport = 0
    conn.role = role
    conn.is_encrypted = encrypted
    conn.on = MagicMock()
    conn.remove_listener = MagicMock()
    return conn


@pytest.mark.anyio
async def test_on_connection_tracking():
    peer = BtPeer()
    peer._on_connection(_connection(handle=42))

    assert 42 in peer._connections
    conns = json.loads(peer.get_connections())
    assert len(conns) == 1
    assert conns[0]["handle"] == 42
    assert conns[0]["address"] == "11:22:33:44:55:66"


@pytest.mark.anyio
async def test_on_connection_disconnect():
    peer = BtPeer()
    conn = _connection(handle=7, role=0, encrypted=True)
    peer._on_connection(conn)
    assert 7 in peer._connections

    conn.on.call_args[0][1](0x13)

    assert 7 not in peer._connections


@pytest.mark.anyio
async def test_wait_connection_not_running():
    with pytest.raises(BtPeerError, match="not running"):
        await BtPeer().wait_connection()


@pytest.mark.anyio
async def test_wait_connection_timeout():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="no connection within"):
            await peer.wait_connection(timeout=0)
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_connection_returns_an_existing_connection():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        peer._on_connection(_connection(handle=3))
        assert json.loads(await peer.wait_connection())["handle"] == 3
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_not_running():
    with pytest.raises(BtPeerError, match="not running"):
        await BtPeer().wait_disconnection()


@pytest.mark.anyio
async def test_wait_disconnection_no_connections():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        assert json.loads(await peer.wait_disconnection())["status"] == "no_connections"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_removes_handlers():
    peer = BtPeer()
    conn = _connection()
    peer._connections[1] = conn
    peer._device = MagicMock()

    with pytest.raises(BtPeerError, match="no disconnection within"):
        await peer.wait_disconnection(timeout=0)

    conn.on.assert_called_once()
    assert conn.on.call_args.args[0] == "disconnection"
    conn.remove_listener.assert_called_once_with("disconnection", conn.on.call_args.args[1])


@pytest.mark.anyio
async def test_pair_not_running():
    with pytest.raises(BtPeerError, match="not running"):
        await BtPeer().pair()


@pytest.mark.anyio
async def test_pair_no_connection():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        with pytest.raises(BtPeerError, match="no connection with handle"):
            await peer.pair(99)
        await peer.stop_peer()


@pytest.mark.anyio
async def test_pair_success():
    peer = BtPeer()
    conn = _connection(encrypted=True)
    conn.authenticate = AsyncMock()
    conn.encrypt = AsyncMock()
    peer._on_connection(conn)

    with patched_stack():
        await peer.start_peer()
        result = json.loads(await peer.pair(1))
        assert result == {"handle": 1, "address": "11:22:33:44:55:66", "encrypted": True}
        conn.authenticate.assert_awaited_once()
        conn.encrypt.assert_awaited_once()
        await peer.stop_peer()


@pytest.mark.anyio
async def test_connect_to_not_running():
    with pytest.raises(BtPeerError, match="not running"):
        await BtPeer().connect_to("AA:BB:CC:DD:EE:FF")


@pytest.mark.anyio
async def test_connect_to():
    peer = BtPeer()
    device = make_device()
    device.connect = AsyncMock(return_value=_connection(handle=5, address="AA:BB:CC:DD:EE:FF"))

    with patched_stack(device=device):
        await peer.start_peer()
        result = json.loads(await peer.connect_to("AA:BB:CC:DD:EE:FF"))
        assert result["handle"] == 5
        assert result["role"] == "central"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_emit_events():
    peer = BtPeer()
    peer._emit("test_event", {"key": "value"})
    assert peer._events[0]["type"] == "test_event"
    assert peer._events[0]["key"] == "value"
    assert "ts" in peer._events[0]


# -- the AVDTP session ---------------------------------------------------


@pytest.mark.anyio
async def test_on_avdtp_connection_creates_a_session():
    peer = BtPeer()
    protocol = fake_protocol()

    peer._on_avdtp_connection(protocol)

    assert peer._session is not None
    protocol.add_source.assert_called_once()
    assert any(e["type"] == "avdtp_connected" for e in peer._events)


@pytest.mark.anyio
async def test_avdtp_close_drops_the_session():
    """A session outliving its channel leaves play() on a dead protocol."""
    peer = BtPeer()
    protocol = fake_protocol()

    peer._on_avdtp_connection(protocol)
    assert peer._session is not None

    event, handler = protocol.l2cap_channel.on.call_args.args
    assert event == ClassicChannel.EVENT_CLOSE
    handler()

    assert peer._session is None
    assert any(e["type"] == "avdtp_disconnected" for e in peer._events)


# -- play ----------------------------------------------------------------


@pytest.mark.anyio
async def test_play_requires_running_peer():
    with pytest.raises(BtPeerError, match="peer not running"):
        await BtPeer().play("handle")


@pytest.mark.anyio
async def test_play_rejects_empty_upload():
    peer = running_peer()
    with patch_resource(b""), pytest.raises(BtPeerError, match="empty"):
        await peer.play("handle")


@pytest.mark.anyio
async def test_play_rejects_oversized_upload():
    peer = running_peer()
    with (
        patch("jumpstarter_driver_bt_peer.driver.MAX_TRACK_BYTES", 1024),
        patch_resource(make_sbc_file(100)),
        pytest.raises(BtPeerError, match="larger than"),
    ):
        await peer.play("handle")


@pytest.mark.anyio
async def test_play_rejects_mismatched_sbc():
    peer = running_peer()
    with patch_resource(make_sbc_file(4, sf_index=3)), pytest.raises(BtPeerError, match="48000 Hz"):
        await peer.play("handle")
    assert peer._player.state == Idle()


@pytest.mark.anyio
async def test_play_sets_track_and_reports_status():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", loop=True))

    assert result["name"] == "song.mp3"
    assert result["loop"] is True
    assert result["bitpool"] == 52
    assert isinstance(peer._player.state, Playing)

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    assert json.loads(peer.playback_status()) == {
        "playing": True,
        "paused": False,
        "streaming": True,
        "name": "song.mp3",
        "loop": True,
        "bytes": result["bytes"],
        "position": 0,
        # No tags were supplied, so the title falls back to the file name.
        "title": "song.mp3",
        "artist": "",
        "album": "",
        "duration_ms": 0,
        "position_ms": 0,
    }

    assert json.loads(await peer.stop_playback())["status"] == "stopped"
    assert peer._player.state == Idle()
    protocol.stream.state = State.OPEN
    assert json.loads(peer.playback_status()) == {"playing": False, "paused": False, "streaming": False}
    assert json.loads(await peer.stop_playback())["status"] == "not_playing"


@pytest.mark.anyio
async def test_play_reports_that_it_started_the_stream():
    peer, _protocol = peer_with_session()

    with patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3"))

    assert result["streaming"] is True
    assert result["reason"] == "started by the peer"


@pytest.mark.anyio
async def test_play_reports_playing_over_avrcp():
    peer, _protocol = peer_with_session()

    with patch_resource(make_sbc_file(100)):
        await peer.play("handle", name="song.mp3")

    assert delegate_of(peer).playback_status == PlayStatus.PLAYING
    avrcp_mock(peer).notify_playback_status_changed.assert_called_with(PlayStatus.PLAYING)

    await peer.stop_playback()
    assert delegate_of(peer).playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_play_does_not_claim_a_stream_without_one():
    """A queued track is not streaming yet; saying so would mislead the caller."""
    peer = running_peer()

    with patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3"))

    assert result["streaming"] is False
    assert result["reason"] == "no AVDTP connection from the DUT yet"


@pytest.mark.anyio
async def test_avrcp_notification_failure_does_not_break_play():
    """A controller that never registered must not fail the play call."""
    peer = running_peer()
    avrcp_mock(peer).notify_playback_status_changed.side_effect = RuntimeError("no listener")

    with patch_resource(make_sbc_file(10)):
        assert json.loads(await peer.play("handle"))["bytes"] > 0


@pytest.mark.anyio
async def test_play_publishes_metadata_over_avrcp():
    from bumble.avrcp import MediaAttributeId

    peer = running_peer()
    metadata = json.dumps({
        "title": "Fun Fun Music",
        "artist": "gr0za",
        "album": "Jumpstarter bt_peer test",
        "duration_ms": 128040,
    })
    with patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", metadata_json=metadata))

    assert result["title"] == "Fun Fun Music"
    assert result["artist"] == "gr0za"
    assert result["duration_ms"] == 128040

    attributes = await delegate_of(peer).get_media_attributes([MediaAttributeId.TITLE])
    assert attributes[0].attribute_value == "Fun Fun Music"
    assert await delegate_of(peer).get_song_length_and_position() == (128040, 0)
    avrcp_mock(peer).notify_track_changed.assert_called_once()


@pytest.mark.anyio
async def test_failed_play_does_not_leave_a_track_installed():
    """A track left behind would make playback_status claim to be playing."""
    peer, protocol = peer_with_session()
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.CONFIGURED
    protocol.stream.start = AsyncMock(side_effect=RuntimeError("the sink went away"))

    with patch_resource(make_sbc_file(50)), pytest.raises(RuntimeError, match="sink went away"):
        await peer.play("handle", name="song.mp3")

    assert peer._player.state == Idle()
    assert json.loads(peer.playback_status())["playing"] is False
    assert delegate_of(peer).playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_failed_play_keeps_the_previous_track():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(50)):
        await peer.play("handle", name="first.mp3")

    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)
    protocol.source.stream = None
    with patch_resource(make_sbc_file(50)), pytest.raises(BtPeerError, match="no free SBC audio sink"):
        await peer.play("handle", name="second.mp3")

    assert peer._player.state.track.name == "first.mp3"
    assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_play_rejects_audio_above_the_negotiated_bitpool():
    """The sink's ceiling can be below the range the peer advertises."""
    peer, _protocol = peer_with_session(sink=sink_with_bitpool(2, 35))

    with patch_resource(make_sbc_file(50, bitpool=52)), pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await peer.play("handle", name="loud.sbc")

    assert peer._player.state == Idle()


@pytest.mark.anyio
async def test_play_rejects_audio_above_the_range_the_dut_configured():
    """The DUT may configure the stream itself, choosing a narrower range.

    `_open_stream` then reuses the configured stream and never inspects the
    sink, so the ceiling has to come from the configuration or a bitpool the
    head unit cannot decode would be streamed at it.
    """
    peer, protocol = peer_with_session()
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.CONFIGURED
    protocol.source.configuration = sink_with_bitpool(2, 35).capabilities

    with patch_resource(make_sbc_file(50, bitpool=52)), pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await peer.play("handle", name="loud.sbc")

    protocol.discover_remote_endpoints.assert_not_called()
    assert peer._player.state == Idle()


@pytest.mark.anyio
async def test_a_rejected_upload_leaves_the_current_track_playing():
    """Validating after suspending would silence audio over a bad upload."""
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="good.sbc")

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    protocol.stream.stop.reset_mock()

    bad = make_sbc_file(4) + make_sbc_file(1, sf_index=3)
    with patch_resource(bad), pytest.raises(BtPeerError, match="48000 Hz"):
        await peer.play("handle", name="bad.sbc")

    protocol.stream.stop.assert_not_awaited()
    assert peer._player.state.track.name == "good.sbc"


@pytest.mark.anyio
async def test_stop_peer_clears_playback():
    peer = running_peer()
    with patch_resource(make_sbc_file(10)):
        await peer.play("handle")
    peer._device = AsyncMock()
    peer._transport = make_transport()
    peer._session = MagicMock()

    await peer.stop_peer()

    assert peer._player.state == Idle()
    assert peer._session is None


# -- pause, resume, stop -------------------------------------------------


@pytest.mark.anyio
async def test_pause_keeps_position_and_resume_continues():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(200)):
        await peer.play("handle", name="song.mp3")

    source = peer._session._media_source()
    for _ in range(10):
        await source.__anext__()
    position = peer._player.state.track.position
    assert position > 0

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    await peer.pause()

    protocol.stream.stop.assert_awaited_once()
    assert peer._player.state.track.position == position, "pause must not rewind the track"

    protocol.stream.state = State.OPEN
    assert json.loads(await peer.resume())["status"] == "resumed"
    assert peer._player.state.track.position == position


@pytest.mark.anyio
async def test_stop_playback_suspends_and_clears_the_track():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING

    assert json.loads(await peer.stop_playback()) == {"status": "stopped", "name": "song.mp3"}
    protocol.stream.stop.assert_awaited_once()
    assert peer._player.state == Idle()
    assert delegate_of(peer).playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_pause_and_resume_without_a_track():
    peer, _protocol = peer_with_session()
    assert json.loads(await peer.pause())["status"] == "not_playing"
    assert json.loads(await peer.resume())["status"] == "not_playing"


@pytest.mark.anyio
async def test_pausing_an_already_paused_track_is_a_no_op():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="song.sbc")
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    await peer.pause()
    protocol.stream.stop.reset_mock()

    assert json.loads(await peer.pause()) == {"status": "paused", "name": "song.sbc"}
    protocol.stream.stop.assert_not_awaited()


@pytest.mark.anyio
async def test_paused_is_reported_and_survives_reconnect():
    """A paused track must not read as playing, nor restart on reconnect."""
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    await peer.pause()

    status = json.loads(peer.playback_status())
    assert status["playing"] is False
    assert status["paused"] is True
    assert peer._player.loaded is not None

    # The DUT drops and reconnects; the track stays paused.
    peer._on_avdtp_close()
    reconnected = fake_protocol()
    peer._on_avdtp_connection(reconnected)
    await settle()

    reconnected.stream.start.assert_not_awaited()
    assert json.loads(peer.playback_status())["paused"] is True

    # And an explicit resume still works.
    await peer.resume()
    assert json.loads(peer.playback_status())["paused"] is False


@pytest.mark.anyio
async def test_play_clears_a_previous_pause():
    peer, _protocol = peer_with_session()
    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="first.sbc")
    await peer.pause()

    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="second.sbc")

    assert json.loads(peer.playback_status())["playing"] is True


# -- transport keys ------------------------------------------------------


def _key(name: str):
    key = MagicMock()
    key.name = name
    return key


@pytest.mark.anyio
async def test_avrcp_transport_keys_are_ignored_by_default():
    """A DUT that sends PAUSE on connect must not silence an explicit play()."""
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")
    protocol.stream.stop.reset_mock()

    peer._on_avrcp_key(_key("PAUSE"), pressed=True)
    await settle(20)

    protocol.stream.stop.assert_not_awaited()
    assert delegate_of(peer).playback_status == PlayStatus.PLAYING
    event = [e for e in peer._events if e["type"] == "avrcp_key"][-1]
    assert event == {**event, "key": "PAUSE", "pressed": True, "honored": False}


@pytest.mark.anyio
async def test_avrcp_transport_keys_suspend_and_restart_the_stream():
    """Pause is an AVDTP SUSPEND, not filler audio; play restarts the stream."""
    peer, protocol = peer_with_session()
    peer.follow_transport_keys = True
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    protocol.stream.start.reset_mock()

    peer._on_avrcp_key(_key("PAUSE"), pressed=True)
    await settle(20)

    protocol.stream.stop.assert_awaited_once()
    assert delegate_of(peer).playback_status == PlayStatus.PAUSED
    assert peer._player.loaded is not None, "pause keeps the track"

    protocol.stream.state = State.OPEN
    peer._on_avrcp_key(_key("PLAY"), pressed=True)
    await settle(20)

    protocol.stream.start.assert_awaited_once()
    assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_a_stop_key_clears_the_track():
    peer, _protocol = peer_with_session()
    peer.follow_transport_keys = True
    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="song.sbc")

    peer._on_avrcp_key(_key("STOP"), pressed=True)
    await settle(20, until=lambda: peer._player.state == Idle())

    assert peer._player.state == Idle()


@pytest.mark.anyio
async def test_a_released_key_does_nothing():
    peer, _protocol = peer_with_session()
    peer.follow_transport_keys = True
    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="song.sbc")

    peer._on_avrcp_key(_key("PAUSE"), pressed=False)
    await settle(20)

    assert delegate_of(peer).playback_status == PlayStatus.PLAYING


# -- resuming a queued track ---------------------------------------------


@pytest.mark.anyio
async def test_track_queued_before_avdtp_starts_when_it_connects():
    """play() can land between the DUT's ACL connection and its AVDTP channel.

    It then has no protocol to start a stream on. The track must not sit there
    silently until something calls play again.
    """
    peer = running_peer()

    with patch_resource(make_sbc_file(100)):
        assert json.loads(await peer.play("handle", name="song.mp3"))["streaming"] is False

    protocol = fake_protocol()
    peer._on_avdtp_connection(protocol)
    await settle(50, until=lambda: protocol.stream.start.await_count > 0)

    protocol.stream.start.assert_awaited_once()
    assert any(e["type"] == "stream_resumed" for e in peer._events)
    assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_avdtp_connection_without_a_queued_track_starts_nothing():
    peer = BtPeer()
    protocol = fake_protocol()

    peer._on_avdtp_connection(protocol)
    await settle(10)

    protocol.discover_remote_endpoints.assert_not_awaited()
    assert not any(e["type"] == "stream_resumed" for e in peer._events)


@pytest.mark.anyio
async def test_resume_failure_is_reported_not_raised():
    peer, protocol = peer_with_session()
    peer._player.state = Playing(Track(name="t", data=make_sbc_file(10)), TrackMetadata())
    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)

    await peer._resume_queued_track()

    failed = [e for e in peer._events if e["type"] == "stream_resume_failed"]
    assert failed and "no free SBC audio sink" in failed[0]["error"]


@pytest.mark.anyio
async def test_resume_discards_a_track_stopped_while_starting():
    """stop_playback() can land while endpoint discovery is still in flight."""
    peer, protocol = peer_with_session()
    peer._player.state = Playing(Track(name="t", data=make_sbc_file(10)), TrackMetadata())

    async def stop_midway():
        peer._player.state = Idle()

    protocol.discover_remote_endpoints = AsyncMock(side_effect=stop_midway)

    await peer._resume_queued_track()

    assert any(e["type"] == "stream_resume_discarded" for e in peer._events)
    assert not any(e["type"] == "stream_resumed" for e in peer._events)
    assert delegate_of(peer).playback_status != PlayStatus.PLAYING


@pytest.mark.anyio
async def test_a_queued_track_is_rechecked_against_the_discovered_sink():
    """Queued before connection, it was only checked against the advertised max."""
    peer = running_peer()

    # bitpool 52 passes the advertised 2-53 ceiling with no session present.
    with patch_resource(make_sbc_file(40, bitpool=52)):
        assert json.loads(await peer.play("handle", name="loud.sbc"))["streaming"] is False

    # The DUT turns out to accept only 35.
    protocol = fake_protocol(sink=sink_with_bitpool(2, 35))
    peer._on_avdtp_connection(protocol)
    await settle(50)

    protocol.stream.start.assert_not_awaited()
    failed = [e for e in peer._events if e["type"] == "stream_resume_failed"]
    assert failed and "sink accepts 2-35" in failed[0]["error"]


@pytest.mark.anyio
async def test_a_queued_track_the_sink_accepts_still_starts():
    peer = running_peer()
    with patch_resource(make_sbc_file(40, bitpool=52)):
        await peer.play("handle", name="fine.sbc")

    protocol = fake_protocol(sink=sink_with_bitpool(2, 53))
    peer._on_avdtp_connection(protocol)
    await settle(50, until=lambda: protocol.stream.start.await_count > 0)

    protocol.stream.start.assert_awaited_once()


@pytest.mark.anyio
async def test_a_paused_track_is_not_resumed_on_connect():
    peer = running_peer()
    with patch_resource(make_sbc_file(30)):
        await peer.play("handle", name="song.sbc")
    await peer.pause()

    protocol = fake_protocol()
    peer._on_avdtp_connection(protocol)
    await settle(30)

    protocol.stream.start.assert_not_awaited()
    assert json.loads(peer.playback_status())["paused"] is True


def test_the_delegate_follows_the_player_without_being_told():
    """Metadata is read from the player, so it cannot describe a stale track."""
    peer = running_peer()
    assert isinstance(peer._avrcp_delegate, PeerDelegate)

    peer._player.state = Playing(Track(name="t", data=b"x"), TrackMetadata(title="Now"))

    assert peer._player.now_playing()[0].title == "Now"

import contextlib
import json
import logging
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import anyio.lowlevel
import pytest
from bumble.avdtp import State
from bumble.avrcp import PlayStatus
from bumble.l2cap import ClassicChannel

from .a2dp import SbcConfig, parse_sbc, sbc_capabilities
from .conftest import (
    FakeResource,
    avrcp_mock,
    connect_avdtp,
    delegate_of,
    fake_protocol,
    make_device,
    make_sbc_file,
    make_transport,
    patch_resource,
    patched_stack,
    peer_with_session,
    player_of,
    running_of,
    running_peer,
    serving,
    session_of,
    settle,
    sink_with_bitpool,
)
from .driver import AutoAcceptDelegate, BtPeer, BtPeerError
from .player import Idle, Playing
from jumpstarter.common.utils import serve

# -- configuration -------------------------------------------------------


def test_bt_peer_default_transport():
    assert BtPeer().transport == "tcp-client:127.0.0.1:7300"


@pytest.mark.anyio
async def test_pairing_needs_no_one_at_the_peer():
    """The peer stands in for a phone in a lab; nobody is there to confirm a PIN."""
    from bumble.pairing import PairingDelegate

    delegate = AutoAcceptDelegate()

    assert delegate.io_capability == PairingDelegate.IoCapability.NO_OUTPUT_NO_INPUT
    assert await delegate.accept()
    assert await delegate.confirm()
    assert await delegate.compare_numbers(123456, digits=6)


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

    assert peer._running is None
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

    assert peer._running is None
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_start_peer_exception_cleanup_after_device_init():
    peer = BtPeer()
    transport = make_transport()
    device = make_device()
    device.power_on = AsyncMock(side_effect=RuntimeError("power on failed"))

    with patched_stack(device=device, transport=transport), pytest.raises(RuntimeError, match="power on failed"):
        await peer.start_peer()

    assert peer._running is None, "a half-built peer must not be kept"
    device.power_off.assert_awaited_once()
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_concurrent_start_peer_builds_one_device():
    """start_peer awaits the transport before the peer counts as running."""
    peer = BtPeer()
    opened: list[MagicMock] = []

    async def open_slowly(_spec):
        await anyio.lowlevel.checkpoint()
        transport = make_transport()
        opened.append(transport)
        return transport

    results: list[BaseException | str] = []

    async def start():
        try:
            results.append(await peer.start_peer())
        except BtPeerError as exc:
            results.append(exc)

    with (
        patched_stack(),
        patch("jumpstarter_driver_bt_peer.driver.open_transport", new=open_slowly),
    ):
        async with anyio.create_task_group() as tg:
            tg.start_soon(start)
            tg.start_soon(start)

    assert len(opened) == 1, "the second start opened a transport of its own"
    assert sum(isinstance(r, BtPeerError) and "already running" in str(r) for r in results) == 1


@pytest.mark.anyio
async def test_a_stopped_peers_connection_events_do_not_reach_the_next():
    """Handlers are bound to the Running they were registered on."""
    device = make_device()
    peer = BtPeer()
    with patched_stack(device=device):
        await peer.start_peer()
        on_connection = _connection_handler(device)
        await peer.stop_peer()
        await peer.start_peer()

        on_connection(_connection(handle=9))

        assert json.loads(peer.get_connections()) == []
        await peer.stop_peer()


@pytest.mark.anyio
async def test_start_peer_advertises_avrcp_target():
    """Without UUID 110C the DUT leaves its A2DP sink inactive and drops the audio."""
    from bumble.sdp import SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID

    device = make_device()
    with (
        patched_stack(device=device),
        patch("jumpstarter_driver_bt_peer.driver.PeerProtocol") as avrcp_protocol,
    ):
        await BtPeer().start_peer()

    uuids = []
    for record in device.sdp_service_records.values():
        for attribute in record:
            if attribute.id == SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID:
                uuids += [str(v.value) for v in attribute.value.value]

    assert any("110A" in u for u in uuids), f"missing A2DP AudioSource: {uuids}"
    assert any("110C" in u for u in uuids), f"missing AVRCP Target: {uuids}"
    avrcp_protocol.return_value.listen.assert_called_once_with(device)


# -- the session ---------------------------------------------------------


def test_a_lease_that_ends_without_stop_peer_lets_go_of_the_adapter():
    """The next lease builds a driver of its own, and needs the adapter free."""
    device, transport = make_device(), make_transport()

    with patched_stack(device=device, transport=transport), serve(BtPeer()) as client:
        client.start_peer()

    device.power_off.assert_awaited_once()
    transport.close.assert_awaited_once()


def test_a_wedged_adapter_does_not_hold_the_lease_open(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_bt_peer.driver.STOP_STEP_TIMEOUT", 0.1)
    device, transport = make_device(), make_transport()

    async def wedged():
        await anyio.sleep(10)

    device.power_off = AsyncMock(side_effect=wedged)

    with patched_stack(device=device, transport=transport), serve(BtPeer()) as client:
        client.start_peer()
        leaving = time.monotonic()

    assert time.monotonic() - leaving < 5
    # Closing the transport is what frees the adapter, so it is not skipped.
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_leaving_the_session_gives_up_on_a_start_that_never_finishes(monkeypatch, caplog):
    """The deadline must hold even though the stop it bounds is shielded."""
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr("jumpstarter_driver_bt_peer.driver.SESSION_STOP_TIMEOUT", 0.1)
    peer = BtPeer()

    async def open_eventually(_spec):
        await anyio.sleep(10)
        return make_transport()

    with patched_stack(), patch("jumpstarter_driver_bt_peer.driver.open_transport", new=open_eventually):
        async with anyio.create_task_group() as rpcs:
            async with serving(peer):
                # An RPC still running as the session leaves, holding the lifecycle lock.
                rpcs.start_soon(peer.start_peer)
                await settle(until=peer._lifecycle.locked)
                leaving = time.monotonic()

            assert time.monotonic() - leaving < 5
            assert "did not stop within" in caplog.text
            rpcs.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_cancelled_start_lets_go_of_the_adapter():
    """A client that gives up on start_peer must not leave the adapter held."""
    device, transport = make_device(), make_transport()
    device.power_on = AsyncMock(side_effect=anyio.sleep_forever)
    # bumble's power_off awaits the host, so it would see the cancellation.
    device.power_off = AsyncMock(side_effect=anyio.lowlevel.checkpoint)
    peer = BtPeer()

    with patched_stack(device=device, transport=transport), anyio.move_on_after(0.05):
        await peer.start_peer()

    assert peer._running is None
    device.power_off.assert_awaited_once()
    transport.close.assert_awaited_once()


@pytest.mark.anyio
async def test_leaving_the_session_cancels_the_work_device_events_started():
    """bumble's AVDTP commands never time out, so this work could wait forever."""
    peer = running_peer()
    cancelled = anyio.Event()

    async def discover_forever():
        try:
            await anyio.sleep_forever()
        finally:
            cancelled.set()

    async with serving(peer):
        with patch_resource(make_sbc_file(10)):
            await peer.play("handle", name="t")
        protocol = fake_protocol()
        protocol.discover_remote_endpoints = AsyncMock(side_effect=discover_forever)
        connect_avdtp(peer, protocol)
        await settle(until=lambda: protocol.discover_remote_endpoints.await_count > 0)

    assert cancelled.is_set(), "the resume outlived the session that started it"


@pytest.mark.anyio
async def test_a_key_that_fails_is_logged_and_the_peer_carries_on(caplog):
    """Raised into the session's task group, it would stop the peer mid-lease."""
    peer, protocol = peer_with_session()
    peer.follow_transport_keys = True
    async with serving(peer):
        with patch_resource(make_sbc_file(30)):
            await peer.play("handle", name="song.sbc")
        protocol.source.stream = protocol.stream
        protocol.stream.state = State.STREAMING
        protocol.stream.stop = AsyncMock(side_effect=RuntimeError("link lost"))

        with caplog.at_level(logging.ERROR):
            peer._on_avrcp_key(_key("PAUSE"), pressed=True)
            await settle(until=lambda: "link lost" in caplog.text)

        assert "The DUT's PAUSE failed" in caplog.text
        assert peer._running is not None
        protocol.stream.stop = AsyncMock()
        peer._on_avrcp_key(_key("STOP"), pressed=True)
        await settle(until=lambda: player_of(peer).state == Idle())
        assert player_of(peer).state == Idle()


# -- connections ---------------------------------------------------------


@pytest.mark.anyio
async def test_get_address():
    peer = BtPeer()
    with patched_stack(device=make_device("AA:BB:CC:DD:EE:FF")):
        await peer.start_peer()
        assert peer.get_address() == "AA:BB:CC:DD:EE:FF"
        await peer.stop_peer()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    [
        lambda peer: peer.get_address(),
        lambda peer: peer.wait_connection(),
        lambda peer: peer.wait_disconnection(),
        lambda peer: peer.pair(),
        lambda peer: peer.connect_to("AA:BB:CC:DD:EE:FF"),
        lambda peer: peer.play("handle"),
        lambda peer: peer.pause(),
        lambda peer: peer.resume(),
        lambda peer: peer.stop_playback(),
    ],
    ids=[
        "get_address", "wait_connection", "wait_disconnection", "pair", "connect_to",
        "play", "pause", "resume", "stop_playback",
    ],
)
async def test_commands_need_a_running_peer(command):
    with pytest.raises(BtPeerError, match="peer not running — call start_peer first"):
        result = command(BtPeer())
        if hasattr(result, "__await__"):
            await result


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


def test_a_stopped_peer_reports_no_connections():
    """Unlike the other commands, not an error: having none is an answer."""
    assert json.loads(BtPeer().get_connections()) == []


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


def _connection_handler(device):
    """The handler start_peer registers for the device's ACL connections."""
    return next(call.args[1] for call in device.on.call_args_list if call.args[0] == "connection")


@pytest.mark.anyio
async def test_connections_are_tracked_until_they_drop():
    device = make_device()
    peer = BtPeer()
    conn = _connection(handle=42, role=0, encrypted=True)
    with patched_stack(device=device):
        await peer.start_peer()

        _connection_handler(device)(conn)

        assert json.loads(peer.get_connections()) == [
            {"handle": 42, "address": "11:22:33:44:55:66", "transport": "BR/EDR", "encrypted": True}
        ]

        event, on_disconnect = conn.on.call_args.args
        assert event == "disconnection"
        on_disconnect(0x13)

        assert json.loads(peer.get_connections()) == []
        await peer.stop_peer()


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
    device = make_device()
    peer = BtPeer()
    with patched_stack(device=device):
        await peer.start_peer()
        _connection_handler(device)(_connection(handle=3))
        assert json.loads(await peer.wait_connection())["handle"] == 3
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_no_connections():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        assert json.loads(await peer.wait_disconnection())["status"] == "no_connections"
        await peer.stop_peer()


@pytest.mark.anyio
async def test_wait_disconnection_removes_handlers():
    """A handler left on the connection would fire into a finished wait."""
    device = make_device()
    peer = BtPeer()
    conn = _connection()
    with patched_stack(device=device):
        await peer.start_peer()
        _connection_handler(device)(conn)
        conn.on.reset_mock()

        with pytest.raises(BtPeerError, match="no disconnection within"):
            await peer.wait_disconnection(timeout=0)

        conn.on.assert_called_once()
        assert conn.on.call_args.args[0] == "disconnection"
        conn.remove_listener.assert_called_once_with("disconnection", conn.on.call_args.args[1])
        await peer.stop_peer()


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
    device = make_device()
    peer = BtPeer()
    conn = _connection(encrypted=True)
    conn.authenticate = AsyncMock()
    conn.encrypt = AsyncMock()

    with patched_stack(device=device):
        await peer.start_peer()
        _connection_handler(device)(conn)
        result = json.loads(await peer.pair(1))
        assert result == {"handle": 1, "address": "11:22:33:44:55:66", "encrypted": True}
        conn.authenticate.assert_awaited_once()
        conn.encrypt.assert_awaited_once()
        await peer.stop_peer()


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


# -- the AVDTP session ---------------------------------------------------


@pytest.mark.anyio
async def test_avdtp_close_drops_the_session():
    """A session outliving its channel leaves play() on a dead protocol."""
    peer = running_peer()
    protocol = fake_protocol()

    connect_avdtp(peer, protocol)
    assert session_of(peer) is not None
    assert any(e["type"] == "avdtp_connected" for e in peer._events)

    event, handler = protocol.l2cap_channel.on.call_args.args
    assert event == ClassicChannel.EVENT_CLOSE
    handler()

    assert session_of(peer) is None
    assert any(e["type"] == "avdtp_disconnected" for e in peer._events)


@pytest.mark.anyio
async def test_a_late_close_does_not_drop_the_session_that_replaced_it():
    """The DUT can reconnect before the old channel's CLOSE is delivered."""
    peer = running_peer()
    old, new = fake_protocol(), fake_protocol()

    connect_avdtp(peer, old)
    _event, old_close = old.l2cap_channel.on.call_args.args
    connect_avdtp(peer, new)
    live = session_of(peer)

    old_close()

    assert session_of(peer) is live
    assert live is not None and live.protocol is new


# -- play ----------------------------------------------------------------


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
    assert player_of(peer).state == Idle()


def _needs_the_loop_meanwhile(work):
    """Wrap `work` so it can only finish if the event loop keeps running while it does.

    Returns the wrapper and a coroutine that answers it from the loop. Run on
    the loop's own thread, the wrapper blocks the loop it is waiting on and
    fails after its timeout.
    """
    started, answered = threading.Event(), threading.Event()

    def wrapper(*args):
        started.set()
        assert answered.wait(timeout=2), "the event loop was blocked while this ran"
        return work(*args)

    async def answer():
        while not started.is_set():
            await anyio.sleep(0.001)
        answered.set()

    return wrapper, answer


@pytest.mark.anyio
async def test_an_upload_is_parsed_off_the_event_loop():
    peer = running_peer()
    parse, answer = _needs_the_loop_meanwhile(parse_sbc)

    with patch("jumpstarter_driver_bt_peer.a2dp.parse_sbc", parse), patch_resource(make_sbc_file(10)):
        async with anyio.create_task_group() as tg:
            tg.start_soon(answer)
            await peer.play("handle")


@pytest.mark.anyio
async def test_play_sets_track_and_reports_status():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(100)):
        result = json.loads(await peer.play("handle", name="song.mp3", loop=True))

    assert result["name"] == "song.mp3"
    assert result["loop"] is True
    assert result["bitpool"] == 52
    assert (result["streaming"], result["reason"]) == (True, "started by the peer")
    assert isinstance(player_of(peer).state, Playing)

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
    assert player_of(peer).state == Idle()
    protocol.stream.state = State.OPEN
    assert json.loads(peer.playback_status()) == {"playing": False, "paused": False, "streaming": False}
    assert json.loads(await peer.stop_playback())["status"] == "not_playing"


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
async def test_failed_play_keeps_the_previous_track():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(50)):
        await peer.play("handle", name="first.mp3")

    protocol.find_remote_sink_by_codec = MagicMock(return_value=None)
    protocol.source.stream = None
    with patch_resource(make_sbc_file(50)), pytest.raises(BtPeerError, match="no free SBC audio sink"):
        await peer.play("handle", name="second.mp3")

    playing = player_of(peer).track
    assert playing is not None and playing.name == "first.mp3"
    assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_play_rejects_audio_above_the_negotiated_bitpool():
    """The sink's ceiling can be below the range the peer advertises."""
    peer, _protocol = peer_with_session(sink=sink_with_bitpool(2, 35))

    with patch_resource(make_sbc_file(50, bitpool=52)), pytest.raises(BtPeerError, match="sink accepts 2-35"):
        await peer.play("handle", name="loud.sbc")

    assert player_of(peer).state == Idle()


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
    playing = player_of(peer).track
    assert playing is not None and playing.name == "good.sbc"


@pytest.mark.anyio
async def test_a_stopped_peer_plays_nothing_and_starts_again_with_nothing():
    peer = BtPeer()
    with patched_stack():
        await peer.start_peer()
        with patch_resource(make_sbc_file(10)):
            await peer.play("handle")

        await peer.stop_peer()
        assert json.loads(peer.playback_status()) == {"playing": False, "paused": False, "streaming": False}

        await peer.start_peer()
        assert json.loads(peer.playback_status())["playing"] is False
        await peer.stop_peer()


@pytest.mark.anyio
async def test_a_track_the_dut_suspends_carries_on_when_the_dut_starts_it():
    """The state is the peer's intent; the DUT may stop and start under it."""
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(40)):
        await peer.play("handle", name="song.sbc")
    session = session_of(peer)
    assert session is not None

    # A SUSPEND from the DUT: bumble stops the pump and leaves the stream OPEN.
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.OPEN
    status = json.loads(peer.playback_status())
    assert (status["playing"], status["streaming"]) == (True, False)

    # Its START rebuilds the packet source, which picks the same track up.
    packet = await anext(session.packets(672))
    assert packet.payload[0] > 0, "the restarted stream sent filler, not the track"
    playing = player_of(peer).track
    assert playing is not None and playing.position > 0


# -- pause, resume, stop -------------------------------------------------


@pytest.mark.anyio
async def test_pause_keeps_position_and_resume_continues():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(200)):
        await peer.play("handle", name="song.mp3")

    session = session_of(peer)
    assert session is not None
    source = session._media_source()
    for _ in range(10):
        await source.__anext__()
    track = player_of(peer).track
    assert track is not None
    position = track.position
    assert position > 0

    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING
    await peer.pause()

    protocol.stream.stop.assert_awaited_once()
    assert track.position == position, "pause must not rewind the track"

    protocol.stream.state = State.OPEN
    assert json.loads(await peer.resume())["status"] == "resumed"
    assert player_of(peer).track is track
    assert track.position == position


@pytest.mark.anyio
async def test_stop_playback_suspends_and_clears_the_track():
    peer, protocol = peer_with_session()
    with patch_resource(make_sbc_file(60)):
        await peer.play("handle", name="song.mp3")
    protocol.source.stream = protocol.stream
    protocol.stream.state = State.STREAMING

    assert json.loads(await peer.stop_playback()) == {"status": "stopped", "name": "song.mp3"}
    protocol.stream.stop.assert_awaited_once()
    assert player_of(peer).state == Idle()
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
    assert player_of(peer).loaded is not None

    # The DUT drops and reconnects; the track stays paused.
    session = session_of(peer)
    assert session is not None
    peer._on_avdtp_close(running_of(peer), session)
    reconnected = fake_protocol()
    connect_avdtp(peer, reconnected)
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
    async with serving(peer):
        peer.follow_transport_keys = True
        with patch_resource(make_sbc_file(60)):
            await peer.play("handle", name="song.mp3")

        protocol.source.stream = protocol.stream
        protocol.stream.state = State.STREAMING
        protocol.stream.start.reset_mock()

        peer._on_avrcp_key(_key("PAUSE"), pressed=True)
        await settle(until=lambda: protocol.stream.stop.await_count > 0)

        protocol.stream.stop.assert_awaited_once()
        assert delegate_of(peer).playback_status == PlayStatus.PAUSED
        assert player_of(peer).loaded is not None, "pause keeps the track"

        protocol.stream.state = State.OPEN
        peer._on_avrcp_key(_key("PLAY"), pressed=True)
        await settle(until=lambda: protocol.stream.start.await_count > 0)

        protocol.stream.start.assert_awaited_once()
        assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_a_stop_key_clears_the_track():
    peer, _protocol = peer_with_session()
    async with serving(peer):
        peer.follow_transport_keys = True
        with patch_resource(make_sbc_file(30)):
            await peer.play("handle", name="song.sbc")

        peer._on_avrcp_key(_key("STOP"), pressed=True)
        await settle(until=lambda: player_of(peer).state == Idle())

        assert player_of(peer).state == Idle()


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
    async with serving(peer):

        with patch_resource(make_sbc_file(100)):
            assert json.loads(await peer.play("handle", name="song.mp3"))["streaming"] is False

        protocol = fake_protocol()
        connect_avdtp(peer, protocol)
        await settle(until=lambda: protocol.stream.start.await_count > 0)

        protocol.stream.start.assert_awaited_once()
        assert any(e["type"] == "stream_resumed" for e in peer._events)
        assert delegate_of(peer).playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_avdtp_connection_without_a_queued_track_starts_nothing():
    peer = running_peer()
    protocol = fake_protocol()

    connect_avdtp(peer, protocol)
    await settle(10)

    protocol.discover_remote_endpoints.assert_not_awaited()
    assert not any(e["type"] == "stream_resumed" for e in peer._events)


@pytest.mark.anyio
async def test_a_stop_during_endpoint_discovery_still_stops_the_track():
    """stop_playback() can arrive while the queued track's stream is being configured.

    It waits for that start to finish rather than racing it, so the track ends
    stopped and the DUT is told so, whichever order the two were asked in.
    """
    peer = running_peer()
    async with serving(peer):
        with patch_resource(make_sbc_file(10)):
            await peer.play("handle", name="t")
        discovered = anyio.Event()

        async def discover_slowly():
            await discovered.wait()

        protocol = fake_protocol()
        protocol.discover_remote_endpoints = AsyncMock(side_effect=discover_slowly)
        connect_avdtp(peer, protocol)
        await settle(until=lambda: protocol.discover_remote_endpoints.await_count > 0)

        stopped = []
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: _outcome(stopped, peer.stop_playback()))
            await settle()
            discovered.set()

        assert json.loads(stopped[0])["status"] == "stopped"
        assert player_of(peer).state == Idle()
        assert delegate_of(peer).playback_status == PlayStatus.STOPPED


async def _outcome(results: list, coro) -> None:
    """Record what `coro` returned or raised, so a test can assert on it after its task group."""
    try:
        results.append(await coro)
    except BtPeerError as exc:
        results.append(exc)


@pytest.mark.anyio
async def test_a_track_starts_on_the_session_that_checked_it():
    """The DUT can reconnect while play() is still configuring its stream."""
    peer, first = peer_with_session(sink=sink_with_bitpool(2, 53))
    configured = anyio.Event()

    async def discover_slowly():
        await configured.wait()

    first.discover_remote_endpoints = AsyncMock(side_effect=discover_slowly)
    narrower = fake_protocol(sink=sink_with_bitpool(2, 35))
    async with serving(peer):
        with patch_resource(make_sbc_file(40, bitpool=52)):
            async with anyio.create_task_group() as tg:
                tg.start_soon(peer.play, "handle")
                await settle(until=lambda: first.discover_remote_endpoints.await_count > 0)
                connect_avdtp(peer, narrower)
                configured.set()
        await settle(until=lambda: any(e["type"] == "stream_resume_failed" for e in peer._events))

        # Checked against the first session's 2-53 and started there. The
        # session that replaced it accepts only 35: it checks the track for
        # itself once play() is done, rejects it, and never streams it.
        first.stream.start.assert_awaited_once()
        narrower.stream.start.assert_not_awaited()
        assert player_of(peer).state == Idle()


@pytest.mark.anyio
async def test_a_dut_that_starts_the_stream_itself_is_sent_nothing_it_did_not_agree_to():
    """A head unit may configure and start the stream before the peer resumes the queued track."""
    peer = running_peer()
    async with serving(peer):
        with patch_resource(make_sbc_file(10, bitpool=52)):
            await peer.play("handle", name="loud.sbc")  # queued, checked against the advertised 2-53
        protocol = fake_protocol()
        protocol.source.configuration = [sbc_capabilities(SbcConfig(2, 35))]  # the DUT's own SET_CONFIGURATION
        connect_avdtp(peer, protocol)
        session = session_of(peer)
        assert session is not None

        # The DUT's START, before the peer's resume has run.
        packet = await anext(session.packets(672))

        assert packet.payload == b"\x00", "the stream carried the track its sink did not agree to"
        assert player_of(peer).state == Idle()
        assert delegate_of(peer).playback_status == PlayStatus.STOPPED
        assert any(e["type"] == "track_rejected" for e in peer._events)
        protocol.stream.start.assert_not_awaited()


@pytest.mark.anyio
async def test_a_dut_that_connects_while_play_is_starting_another_session_gets_the_track():
    """Connected mid-transition, the new session sees no queued track yet; it must not be left idle."""
    peer, first = peer_with_session()
    configured = anyio.Event()

    async def discover_slowly():
        await configured.wait()

    first.discover_remote_endpoints = AsyncMock(side_effect=discover_slowly)
    second = fake_protocol()
    async with serving(peer):
        with patch_resource(make_sbc_file(10)):
            async with anyio.create_task_group() as tg:
                tg.start_soon(peer.play, "handle")
                await settle(until=lambda: first.discover_remote_endpoints.await_count > 0)
                connect_avdtp(peer, second)
                configured.set()
        await settle(until=lambda: second.stream.start.await_count > 0)

        second.stream.start.assert_awaited_once()
        assert isinstance(player_of(peer).state, Playing)

@pytest.mark.anyio
async def test_a_replaced_sessions_failure_does_not_stop_its_replacement():
    """A dead channel says nothing about the track, so the next session plays it."""
    peer = running_peer()
    async with serving(peer):
        with patch_resource(make_sbc_file(10)):
            await peer.play("handle", name="t")
        closed = anyio.Event()

        async def discover_until_closed():
            await closed.wait()
            raise RuntimeError("the channel closed")

        old = fake_protocol()
        old.discover_remote_endpoints = AsyncMock(side_effect=discover_until_closed)
        connect_avdtp(peer, old)
        await settle(until=lambda: old.discover_remote_endpoints.await_count > 0)
        replacement = fake_protocol()
        connect_avdtp(peer, replacement)
        closed.set()
        await settle(until=lambda: replacement.stream.start.await_count > 0)

        replacement.stream.start.assert_awaited_once()
        assert isinstance(player_of(peer).state, Playing)


@pytest.mark.anyio
async def test_a_play_that_outlives_its_peer_fails_and_leaves_the_next_run_alone():
    """stop_peer, and a new start_peer, can land while a track is still uploading."""
    peer = BtPeer()
    uploaded = anyio.Event()

    @contextlib.asynccontextmanager
    async def slow_upload(self, handle, timeout=7200):
        await uploaded.wait()
        yield FakeResource(make_sbc_file(10))

    results: list = []
    with patched_stack(), patch.object(BtPeer, "resource", slow_upload):
        await peer.start_peer()
        stopped_runs_channel = fake_protocol()
        connect_avdtp(peer, stopped_runs_channel)
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: _outcome(results, peer.play("handle", name="old-run.sbc")))
            await settle()
            await peer.stop_peer()
            await peer.start_peer()
            uploaded.set()

        assert "stopped while the track was being prepared" in str(results[0])
        assert json.loads(peer.playback_status())["playing"] is False
        # Nor was the stopped run's stream driven, on a device that powered off.
        stopped_runs_channel.stream.start.assert_not_awaited()
        await peer.stop_peer()

@pytest.mark.anyio
async def test_a_queued_track_is_rechecked_against_the_discovered_sink():
    """Queued before connection, it was only checked against the advertised max."""
    peer = running_peer()
    async with serving(peer):

        # bitpool 52 passes the advertised 2-53 ceiling with no session present.
        with patch_resource(make_sbc_file(40, bitpool=52)):
            assert json.loads(await peer.play("handle", name="loud.sbc"))["streaming"] is False

        # The DUT turns out to accept only 35.
        protocol = fake_protocol(sink=sink_with_bitpool(2, 35))
        connect_avdtp(peer, protocol)
        await settle(until=lambda: any(e["type"] == "stream_resume_failed" for e in peer._events))

        protocol.stream.start.assert_not_awaited()
        failed = [e for e in peer._events if e["type"] == "stream_resume_failed"]
        assert failed and "sink accepts 2-35" in failed[0]["error"]
        # Dropped, not left Playing: a START from the DUT would pump it.
        assert player_of(peer).state == Idle()
        assert player_of(peer).track is None
        assert json.loads(peer.playback_status())["playing"] is False


@pytest.mark.anyio
async def test_a_queued_track_that_fails_to_start_stays_queued():
    """A stream that will not start says nothing about the track itself."""
    peer = running_peer()
    async with serving(peer):
        with patch_resource(make_sbc_file(10)):
            await peer.play("handle", name="t")
        queued = player_of(peer).state

        protocol = fake_protocol()
        protocol.stream.start = AsyncMock(side_effect=RuntimeError("channel gone"))
        connect_avdtp(peer, protocol)
        await settle(until=lambda: any(e["type"] == "stream_resume_failed" for e in peer._events))

        assert player_of(peer).state is queued
        failed = [e for e in peer._events if e["type"] == "stream_resume_failed"]
        assert failed and "channel gone" in failed[0]["error"]

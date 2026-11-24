"""The playback state machine, exercised without a driver around it."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from bumble.avrcp import PlayStatus

from .a2dp import ADVERTISED, SbcConfig, Track
from .avrcp import PeerDelegate, Reporter, TrackMetadata
from .conftest import make_sbc_file
from .errors import TrackRejected
from .player import Idle, Paused, Player, Playing


def _player(session=None):
    """A player over a stub session, reporting to a real delegate."""
    delegate = PeerDelegate(on_key=lambda key, pressed: None, now_playing=lambda: (TrackMetadata(), 0))
    reporter = Reporter(delegate, MagicMock(), logging.getLogger("test"))
    events: list[tuple[str, dict]] = []
    player = Player(
        get_session=lambda: session,
        reporter=reporter,
        emit=lambda event_type, data=None: events.append((event_type, data or {})),
    )
    return player, delegate, events


def _session(streaming: bool = False):
    session = MagicMock()
    session.is_streaming = streaming
    session.prepare = AsyncMock(return_value=ADVERTISED)
    session.start = AsyncMock(return_value={"streaming": True, "reason": "started by the peer"})
    session.suspend = AsyncMock(return_value=True)
    return session


def _track(name: str = "song.sbc", loop: bool = False) -> Track:
    return Track(name=name, data=make_sbc_file(10), loop=loop)


@pytest.mark.anyio
async def test_a_new_player_is_idle():
    player, _delegate, _events = _player()

    assert player.state == Idle()
    assert player.track is None
    assert player.describe() == {"playing": False, "paused": False, "streaming": False}


@pytest.mark.anyio
async def test_playing_starts_the_stream_and_tells_the_dut():
    session = _session()
    player, delegate, _events = _player(session)
    target = Playing(_track(), TrackMetadata(title="T"))

    result = await player.transition(player.state, target)

    assert result == {"streaming": True, "reason": "started by the peer"}
    session.start.assert_awaited_once()
    assert player.state is target
    assert delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_pausing_suspends_and_withholds_the_track_from_the_pump():
    """A paused track is kept at its position, not fed to the packet source."""
    session = _session()
    player, delegate, _events = _player(session)
    track = _track()
    playing = Playing(track, TrackMetadata())
    await player.transition(player.state, playing)

    result = await player.transition(playing, Paused(track, TrackMetadata()))

    assert result == {"suspended": True}
    assert player.track is None, "the pump must not keep pulling a paused track"
    assert player.loaded is not None, "but the track itself is kept"
    assert delegate.playback_status == PlayStatus.PAUSED


@pytest.mark.anyio
async def test_a_stale_transition_is_refused():
    """Two callers racing must not leave the loser's state installed."""
    session = _session()
    player, _delegate, _events = _player(session)
    first = Playing(_track("first.sbc"), TrackMetadata())
    stale = player.state

    await player.transition(stale, first)
    second = Playing(_track("second.sbc"), TrackMetadata())

    assert await player.transition(stale, second) is None
    assert player.state is first


@pytest.mark.anyio
async def test_a_failed_start_leaves_the_state_untouched():
    """A play that could not start must not leave a track installed."""
    session = _session()
    session.start = AsyncMock(side_effect=RuntimeError("no sink"))
    player, delegate, _events = _player(session)

    with pytest.raises(RuntimeError, match="no sink"):
        await player.transition(player.state, Playing(_track(), TrackMetadata()))

    assert player.state == Idle()
    # And the DUT is told where the peer actually is, not where it was going.
    assert delegate.playback_status == PlayStatus.STOPPED


@pytest.mark.anyio
async def test_a_failed_swap_keeps_the_track_that_was_playing():
    session = _session()
    player, delegate, _events = _player(session)
    first = Playing(_track("first.sbc"), TrackMetadata())
    await player.transition(player.state, first)
    session.start = AsyncMock(side_effect=RuntimeError("gone"))

    with pytest.raises(RuntimeError):
        await player.transition(first, Playing(_track("second.sbc"), TrackMetadata()))

    assert player.state is first
    assert delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_swapping_tracks_restarts_the_stream():
    """A new track must not splice into a stream already in flight."""
    session = _session()
    player, _delegate, _events = _player(session)
    first = Playing(_track("first.sbc"), TrackMetadata())
    await player.transition(player.state, first)
    session.suspend.reset_mock()

    await player.transition(first, Playing(_track("second.sbc"), TrackMetadata()))

    session.suspend.assert_awaited_once()
    assert session.start.await_count == 2


@pytest.mark.anyio
async def test_re_affirming_the_same_track_does_not_suspend_it():
    """Resuming a track that is already playing must not interrupt it."""
    session = _session()
    player, _delegate, _events = _player(session)
    playing = Playing(_track(), TrackMetadata())
    await player.transition(player.state, playing)
    session.suspend.reset_mock()

    await player.transition(playing, playing)

    session.suspend.assert_not_awaited()
    assert session.start.await_count == 2


@pytest.mark.anyio
async def test_playing_without_a_session_queues_the_track():
    player, delegate, _events = _player(session=None)
    target = Playing(_track(), TrackMetadata())

    result = await player.transition(player.state, target)

    assert result == {"streaming": False, "reason": "no AVDTP connection from the DUT yet"}
    assert player.state is target
    # The DUT is told it is playing: that is the peer's intent, and the stream
    # starts as soon as the DUT's AVDTP channel arrives.
    assert delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
async def test_a_track_running_out_falls_back_to_filler():
    session = _session()
    player, delegate, events = _player(session)
    track = _track()
    await player.transition(player.state, Playing(track, TrackMetadata()))

    player.on_track_exhausted(track)

    assert player.state == Idle()
    assert delegate.playback_status == PlayStatus.STOPPED
    assert ("track_finished", {"name": track.name}) in events
    # It runs on the pump's task, so it must not stop the stream from there.
    session.suspend.assert_not_awaited()


@pytest.mark.anyio
async def test_a_looping_track_rewinds_instead_of_finishing():
    player, _delegate, events = _player(_session())
    track = _track(loop=True)
    playing = Playing(track, TrackMetadata())
    await player.transition(player.state, playing)
    track.next_payload(672)

    player.on_track_exhausted(track)

    assert player.state is playing
    assert track.position == 0
    assert ("track_looped", {"name": track.name}) in events


@pytest.mark.anyio
async def test_a_track_that_is_no_longer_current_does_not_clear_the_new_one():
    """play() can swap tracks while the old one is still draining."""
    player, _delegate, _events = _player(_session())
    old = _track("old.sbc")
    await player.transition(player.state, Playing(old, TrackMetadata()))
    current = Playing(_track("new.sbc"), TrackMetadata())
    await player.transition(player.state, current)

    player.on_track_exhausted(old)

    assert player.state is current


@pytest.mark.anyio
async def test_a_track_draining_mid_transition_is_not_resurrected():
    """on_track_exhausted is the one writer that does not take the lock."""
    session = _session()
    player, _delegate, _events = _player(session)
    track = _track()
    playing = Playing(track, TrackMetadata())
    await player.transition(player.state, playing)

    async def drain_midway():
        player.on_track_exhausted(track)
        return {"streaming": True, "reason": "started by the peer"}

    session.start = AsyncMock(side_effect=drain_midway)

    assert await player.transition(playing, playing) is None
    assert player.state == Idle()


@pytest.mark.anyio
async def test_position_tracks_bytes_consumed():
    """SBC at a fixed bitpool is constant bitrate, so bytes scale with time."""
    player, _delegate, _events = _player(_session())
    track = Track(name="t", data=make_sbc_file(200))
    await player.transition(player.state, Playing(track, TrackMetadata(duration_ms=10000)))

    assert player.position_ms() == 0

    while track.position < track.size // 4:
        track.next_payload(672)

    assert 2000 < player.position_ms() < 3000, player.position_ms()
    assert player.now_playing() == (TrackMetadata(duration_ms=10000), player.position_ms())


@pytest.mark.anyio
async def test_a_track_the_sink_cannot_take_is_rejected_before_the_stream_starts():
    """Checked against the session that would play it, under the same lock."""
    session = _session()
    session.prepare = AsyncMock(return_value=SbcConfig(2, 35))
    player, _delegate, _events = _player(session)

    with pytest.raises(TrackRejected, match="sink accepts 2-35"):
        await player.transition(player.state, Playing(_track(), TrackMetadata()))

    assert player.state == Idle()
    session.start.assert_not_awaited()


@pytest.mark.anyio
async def test_a_rejected_swap_leaves_the_playing_track_streaming():
    session = _session()
    player, _delegate, _events = _player(session)
    first = Playing(_track("first.sbc"), TrackMetadata())
    await player.transition(player.state, first)
    session.prepare = AsyncMock(return_value=SbcConfig(2, 35))

    with pytest.raises(TrackRejected):
        await player.transition(first, Playing(_track("second.sbc"), TrackMetadata()))

    assert player.state is first
    session.suspend.assert_not_awaited()


@pytest.mark.anyio
async def test_a_queued_track_is_checked_against_the_advertised_range():
    player, _delegate, _events = _player(session=None)
    too_loud = Track(name="loud.sbc", data=make_sbc_file(10, bitpool=60))

    with pytest.raises(TrackRejected, match="bitpool 60"):
        await player.transition(player.state, Playing(too_loud, TrackMetadata()))

    assert player.state == Idle()

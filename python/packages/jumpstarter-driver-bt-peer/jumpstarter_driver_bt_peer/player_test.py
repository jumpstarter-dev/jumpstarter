"""The playback state machine, exercised without a driver around it."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from bumble.avrcp import PlayStatus

from .a2dp import Track
from .avrcp import PeerDelegate, Reporter, TrackMetadata
from .conftest import make_sbc_file
from .player import Idle, Paused, Player, Playing


def _player(session=None):
    """A player over a stub session, reporting to a real delegate."""
    delegate = PeerDelegate()
    reporter = Reporter(delegate, None, logging.getLogger("test"))
    events: list[tuple[str, dict]] = []
    player = Player(
        get_session=lambda: session,
        get_reporter=lambda: reporter,
        emit=lambda event_type, data=None: events.append((event_type, data or {})),
    )
    return player, delegate, events


def _session(streaming: bool = False):
    session = MagicMock()
    session.is_streaming = streaming
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
    # Queued is not playing: saying otherwise would mislead the DUT.
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
    await track._reader.read(40)

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
async def test_describe_reports_the_track_and_the_stream():
    session = _session(streaming=True)
    player, _delegate, _events = _player(session)
    track = Track(name="song.mp3", data=make_sbc_file(100), loop=True)
    metadata = TrackMetadata(title="T", artist="A", album="B", duration_ms=10000)
    await player.transition(player.state, Playing(track, metadata))

    assert player.describe() == {
        "playing": True,
        "paused": False,
        "streaming": True,
        "name": "song.mp3",
        "loop": True,
        "bytes": len(track.data),
        "position": 0,
        "title": "T",
        "artist": "A",
        "album": "B",
        "duration_ms": 10000,
        "position_ms": 0,
    }


@pytest.mark.anyio
async def test_position_tracks_bytes_consumed():
    """SBC at a fixed bitpool is constant bitrate, so bytes scale with time."""
    player, _delegate, _events = _player(_session())
    track = Track(name="t", data=make_sbc_file(200))
    await player.transition(player.state, Playing(track, TrackMetadata(duration_ms=10000)))

    assert player.position_ms() == 0

    await track._reader.read(len(track.data) // 4)

    assert 2000 < player.position_ms() < 3000, player.position_ms()
    assert player.now_playing() == (TrackMetadata(duration_ms=10000), player.position_ms())


@pytest.mark.anyio
async def test_reset_drops_the_state_without_touching_the_stream():
    session = _session()
    player, _delegate, _events = _player(session)
    await player.transition(player.state, Playing(_track(), TrackMetadata()))

    player.reset()

    assert player.state == Idle()
    session.suspend.assert_not_awaited()

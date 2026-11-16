"""The peer's playback state, and the place that changes it.

What the peer is doing is one value, not a track plus a pair of flags, so
there is no combination to get wrong: a paused track cannot be reported as
playing, and a track cannot linger after a failed start.

The state is what the peer *intends*, not what the AVDTP stream is doing.
The two are separate on purpose. The DUT may suspend the stream and start
it again on its own -- a head unit switching sources, or losing audio
focus -- and when it does, a Playing track should carry on from where it
was. So the state stays Playing under a suspended stream, and the stream's
own state is reported beside it as `streaming`. Every change the peer makes
itself goes through `Player.transition`, which drives the stream and tells
the DUT the result under one lock, so the status the head unit is told and
the status `playback_status` reports always agree.

A player belongs to one run of the peer, from start_peer to stop_peer, and
is dropped with it, so a command still in flight when the peer stops cannot
leave a track behind for the next run.
"""

from dataclasses import dataclass
from typing import Any, assert_never

import anyio
from bumble.avrcp import PlayStatus

from .a2dp import ADVERTISED, A2dpSession, Track
from .avrcp import Reporter, TrackMetadata


@dataclass(frozen=True)
class Idle:
    """Nothing loaded. The stream, if one is open, carries filler."""


@dataclass(frozen=True)
class Loaded:
    """A track the peer holds, whether or not it is being pumped."""

    track: Track
    metadata: TrackMetadata


@dataclass(frozen=True)
class Playing(Loaded):
    """The stream should be running and pulling this track's packets."""


@dataclass(frozen=True)
class Paused(Loaded):
    """Suspended at its current position, and not to be restarted unasked."""


Playback = Idle | Playing | Paused

# What `transition` reports when there is no AVDTP connection to drive. A
# track set now is queued: `_resume_queued_track` picks it up when the DUT's
# channel arrives.
NO_SESSION: dict[str, Any] = {
    "streaming": False,
    "reason": "no AVDTP connection from the DUT yet",
}


def play_status_of(state: Playback) -> PlayStatus:
    match state:
        case Idle():
            return PlayStatus.STOPPED
        case Playing():
            return PlayStatus.PLAYING
        case Paused():
            return PlayStatus.PAUSED
        case _:  # pragma: no cover
            assert_never(state)


class Player:
    """Owns the playback state of one run of the peer.

    `transition` is the writer for every change a caller asks for. One other
    exists and is kept narrow: `on_track_exhausted`, which runs inside the
    packet pump and cannot take the lock.

    `get_session` is read late rather than captured, because the session
    comes and goes with the DUT's AVDTP channel.
    """

    def __init__(self, get_session, reporter: Reporter, emit):
        self.state: Playback = Idle()
        self._get_session = get_session
        self._reporter = reporter
        self._emit = emit
        self._lock = anyio.Lock()

    # -- reading ---------------------------------------------------------

    @property
    def track(self) -> Track | None:
        """The track the packet source should pull from, if any.

        Only a playing track qualifies. A paused one is held at its position
        and must not be fed to the pump.
        """
        state = self.state
        return state.track if isinstance(state, Playing) else None

    @property
    def loaded(self) -> Loaded | None:
        """The track the peer holds, playing or paused."""
        state = self.state
        return state if isinstance(state, Loaded) else None

    def position_ms(self) -> int:
        """Approximate playback position from how much of the buffer was read.

        SBC at a fixed bitpool is constant bitrate, so bytes consumed scale
        with elapsed time.
        """
        loaded = self.loaded
        if loaded is None or not loaded.metadata.duration_ms:
            return 0
        return int(loaded.metadata.duration_ms * loaded.track.position / loaded.track.size)

    def now_playing(self) -> tuple[TrackMetadata, int]:
        """Metadata and position together, for the AVRCP delegate."""
        loaded = self.loaded
        if loaded is None:
            return (TrackMetadata(), 0)
        return (loaded.metadata, self.position_ms())

    def describe(self) -> dict[str, Any]:
        """What the peer intends to play, and whether the stream is carrying it.

        `playing` with `streaming` false is a real state rather than a
        disagreement: the DUT suspended the stream, or a track swap could not
        restart it. The next START, from either side, resumes the track.
        """
        session = self._get_session()
        streaming = session is not None and session.is_streaming
        match self.state:
            case Idle():
                return {"playing": False, "paused": False, "streaming": streaming}
            case Playing():
                return {"playing": True, "paused": False, "streaming": streaming, **self._describe_track()}
            case Paused():
                return {"playing": False, "paused": True, "streaming": streaming, **self._describe_track()}
            case _:  # pragma: no cover
                assert_never(self.state)

    def _describe_track(self) -> dict[str, Any]:
        loaded = self.state
        assert isinstance(loaded, Loaded)
        return {
            "name": loaded.track.name,
            "loop": loaded.track.loop,
            "bytes": loaded.track.size,
            "position": loaded.track.position,
            **loaded.metadata.as_dict(),
            "position_ms": self.position_ms(),
        }

    # -- writing ---------------------------------------------------------

    async def transition(self, expected: Playback, target: Playback) -> dict[str, Any] | None:
        """Move to `target`, unless the state changed since the caller read it.

        `expected` is the state the caller based its decision on, compared by
        identity: anything else means another caller got there first and this
        transition is stale. Returns the stream's state on success, or None
        when it was stale.

        Raises if driving the stream fails, leaving the state untouched -- a
        play that could not start must not leave a track installed. That
        includes `TrackRejected`: a track is checked against the session it is
        about to start on, read once under the lock, so the session that
        accepts a track is the one that plays it, even when the DUT reconnects
        while the stream is being configured.
        """
        async with self._lock:
            if self.state is not expected:
                return None

            session = self._get_session()
            try:
                result = await self._drive(session, target)
            except Exception:
                # Nothing moved, so re-state where we actually are: the DUT
                # may have seen a partial change before the failure.
                self._report(self.state, track_changed=False)
                raise

            if self.state is not expected:
                # A track draining to its end is the one writer that does not
                # take the lock, because it runs inside the packet pump.
                return None

            track_changed = _track_of(target) is not None and _track_of(target) is not _track_of(expected)
            self.state = target
            self._report(target, track_changed=track_changed)
            return result

    async def _drive(self, session: A2dpSession | None, target: Playback) -> dict[str, Any]:
        """Bring the AVDTP stream in line with `target`."""
        match (self.state, target):
            case (_, Playing(track=track)) if session is None:
                # Queued until the DUT's channel arrives, so the advertised
                # range is all there is to check against yet.
                track.check(ADVERTISED)
                return dict(NO_SESSION)
            case (Playing(track=old), Playing(track=new)) if old is not new:
                assert session is not None
                # Checked before the suspend, so a track this sink cannot take
                # leaves the one already playing alone.
                new.check(await session.prepare())
                # A track swap: suspend so the new track restarts the stream
                # cleanly rather than splicing into one already in flight.
                await session.suspend()
                return await session.start()
            case (_, Playing(track=track)):
                assert session is not None
                track.check(await session.prepare())
                return await session.start()
            case (_, Paused() | Idle()):
                return {"suspended": session is not None and await session.suspend()}
            case _:  # pragma: no cover
                assert_never(target)

    def on_track_exhausted(self, track: Track) -> None:
        """The track's packets ran out; called from inside the packet pump.

        Synchronous and lock-free on purpose. It runs on the pump's task, so
        awaiting a suspend here would have the pump stop the task it is
        running on, and blocking on the lock would stall the audio. Dropping
        the track is enough: the next pass through the source yields filler.
        `transition` re-checks the state after driving the stream, so a change
        made here is never overwritten by one that was already in flight.
        """
        if track.loop:
            track.rewind()
            self._emit("track_looped", {"name": track.name})
            return

        state = self.state
        if isinstance(state, Playing) and state.track is track:
            self.state = Idle()
            self._report(self.state, track_changed=False)
        self._emit("track_finished", {"name": track.name})

    def _report(self, state: Playback, track_changed: bool) -> None:
        self._reporter.report(play_status_of(state), track_changed=track_changed)


def _track_of(state: Playback) -> Track | None:
    return state.track if isinstance(state, Loaded) else None

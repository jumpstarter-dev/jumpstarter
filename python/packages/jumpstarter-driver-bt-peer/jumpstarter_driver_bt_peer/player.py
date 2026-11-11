"""The peer's playback state, and the single place that changes it.

What the peer is doing is one value, not a track plus a pair of flags, so
there is no combination to get wrong: a paused track cannot be reported as
playing, and a track cannot linger after a failed start.

`Player.transition` is the only writer. It drives the AVDTP stream and tells
the DUT the result in the same step, under one lock, so the status the head
unit shows and the status `playback_status` reports cannot drift apart.
"""

from dataclasses import dataclass
from typing import Any, assert_never

import anyio
from bumble.avrcp import PlayStatus

from .a2dp import A2dpSession, Track
from .avrcp import TrackMetadata


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
    """Owns the playback state and every change to it.

    `get_session` and `get_reporter` are read late rather than captured,
    because both come and go: the session with the DUT's AVDTP channel, the
    reporter with start_peer.
    """

    def __init__(self, get_session, get_reporter, emit):
        self.state: Playback = Idle()
        self._get_session = get_session
        self._get_reporter = get_reporter
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
        if loaded is None or not loaded.track.data or not loaded.metadata.duration_ms:
            return 0
        return int(loaded.metadata.duration_ms * loaded.track.position / len(loaded.track.data))

    def now_playing(self) -> tuple[TrackMetadata, int]:
        """Metadata and position together, for the AVRCP delegate."""
        loaded = self.loaded
        if loaded is None:
            return (TrackMetadata(), 0)
        return (loaded.metadata, self.position_ms())

    def describe(self) -> dict[str, Any]:
        """What the peer is currently streaming, for playback_status."""
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
            "bytes": len(loaded.track.data),
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
        play that could not start must not leave a track installed.
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
            case (_, Playing()) if session is None:
                return dict(NO_SESSION)
            case (Playing(track=old), Playing(track=new)) if old is not new:
                # A track swap: suspend so the new track restarts the stream
                # cleanly rather than splicing into one already in flight.
                assert session is not None
                await session.suspend()
                return await session.start()
            case (_, Playing()):
                assert session is not None
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

    def reset(self) -> None:
        """Drop the state without touching the stream, for teardown.

        stop_peer is tearing the device down, so there is no AVDTP stream
        left to suspend and no controller left to tell.
        """
        self.state = Idle()

    def _report(self, state: Playback, track_changed: bool) -> None:
        reporter = self._get_reporter()
        if reporter is not None:
            reporter.report(play_status_of(state), track_changed=track_changed)


def _track_of(state: Playback) -> Track | None:
    return state.track if isinstance(state, Loaded) else None

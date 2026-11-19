"""How good the picture is, and when to make it worse or better: pure code (no device, no sockets, no clock of its own).

**The principle: the network between the client and the exporter is what limits the stream, not the configuration.** The
stream starts at the device's own size with the bit rate that suits it (:func:`auto_bit_rate`: 8 Mbit/s at 1920x1080, in
proportion to the picture's pixels), at 30 frames a second or more; ``max_size``, ``bit_rate`` and ``max_fps`` in the
exporter's config are optional **ceilings** ("cap the stream on purpose"), and nothing is capped when they are not set.
From there the stream steps down when the path to the viewers cannot keep up, and back up when it has room again
(:class:`Adapter`). The hub measures the path: a viewer whose queue grows is dropped non-reference pictures, then skips
to the next key picture, then (if it keeps on) gets key pictures only (see ``hub.py``); those are the signals this
module reads.

**One encode, many viewers.** A slow viewer is dealt with by the hub *for that viewer* first (its own pictures are
dropped; the fast viewers do not notice). The encode steps down only when **most** viewers are slow at once, and one
slow viewer in a crowd never makes it. A slow viewer alone is the whole audience, so it does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple

#: What the size is taken to be before the device has said (a common 1080p display), for the bit rate of the first
#: start.
ASSUMED_NATIVE = (1920, 1080)
#: The bit rate that suits 1920x1080 at 30 pictures a second, and the lowest and highest the automatic choice gives.
REFERENCE_PIXELS = 1920 * 1080
REFERENCE_BIT_RATE = 8_000_000
MIN_BIT_RATE = 1_000_000
MAX_BIT_RATE = 20_000_000


class Rung(NamedTuple):
    """One quality setting of the encode. It is still a plain ``(max_size, bit_rate, max_fps)`` tuple, so code that
    unpacks one keeps working."""

    #: The longest side of the picture in pixels; 0 for the device's own size.
    max_size: int
    #: Bits a second.
    bit_rate: int
    #: Pictures a second.
    max_fps: int


#: Named levels below the top, lowest first. The top is not one of these: it is the device's own size at the automatic
#: bit rate (or the operator's ceiling), and it is what ``high`` and ``auto`` mean.
PRESETS: dict[str, Rung] = {
    "minimal": Rung(640, 1_000_000, 15),
    "low": Rung(960, 2_000_000, 24),
    "medium": Rung(1280, 4_000_000, 30),
}
LEVELS = (*PRESETS, "high")


def auto_bit_rate(width: int, height: int, fps: int = 30) -> int:
    """The bit rate that suits a picture of this size: 8 Mbit/s at 1920x1080, in proportion to the pixels, a little more
    above 30 a second."""
    rate = REFERENCE_BIT_RATE * (width * height) / REFERENCE_PIXELS
    if fps > 30:
        rate *= math.sqrt(fps / 30)
    return int(min(MAX_BIT_RATE, max(MIN_BIT_RATE, round(rate, -5))))


def scaled(width: int, height: int, max_size: int) -> tuple[int, int]:
    """The picture's size once the encoder has capped its longest side at ``max_size`` (0: not at all), the way a
    hardware scaler does it: down to an even number (1080x2340 at 1280 is 590x1280, and 1280x2856 is 572x1280)."""
    longest = max(width, height)
    if not max_size or longest <= max_size:
        return width, height
    factor = max_size / longest
    return max(2, int(width * factor) // 2 * 2), max(2, int(height * factor) // 2 * 2)


def top_rung(
    native: tuple[int, int] | None, ceiling_size: int | None, ceiling_rate: int | None, ceiling_fps: int | None
) -> Rung:
    """Where the stream starts: the device's own size (or the operator's cap on it), the bit rate that suits that
    picture (or the operator's cap on it), and 30 pictures a second (or the operator's number)."""
    size = ceiling_size or 0
    fps = ceiling_fps or 30
    width, height = scaled(*(native or ASSUMED_NATIVE), size)
    rate = auto_bit_rate(width, height, fps)
    if ceiling_rate:
        rate = min(rate, ceiling_rate)
    return Rung(size, rate, fps)


def ladder(top: Rung) -> list[Rung]:
    """The rungs from the lowest preset up to ``top`` (never above it), by bit rate; rungs that come to the same thing
    are one."""
    top = Rung(*top)
    rungs = {level_rung(name, top) for name in PRESETS}
    rungs.add(top)
    return sorted(rungs, key=lambda r: (r.bit_rate, r.max_size or 1 << 30, r.max_fps))


def level_rung(name: str, top: Rung) -> Rung:
    """The rung a named level means under this top: ``high`` is the top itself."""
    top = Rung(*top)
    if name == "high":
        return top
    size, rate, fps = PRESETS[name]
    return Rung(min(size, top.max_size) if top.max_size else size, min(rate, top.bit_rate), min(fps, top.max_fps))


# ---- when to step
# ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PathSample:
    """What the hub measured of one viewer's path over a window of time."""

    viewer: int
    #: How many times, in the window, the viewer's queue grew past its limits (its non-reference pictures were dropped,
    #: or it skipped to a key picture).
    events: int
    #: The longest its oldest queued picture waited, as a fraction of the lag limit (1.0: at the limit).
    peak: float
    #: The viewer is getting key pictures only: the path could not take the stream at all.
    keyframes_only: bool


@dataclass(frozen=True, slots=True)
class Decision:
    """A step the stream should take, and why."""

    direction: str  # "down" or "up"
    why: str


class Adapter:
    """Decides when the shared encode steps down and up. It keeps the time of the last step and a back-off, nothing
    else.

    **Down:** most viewers are slow at once (a viewer is slow when it had ``min_events`` queue overflows in
    ``window_s``, or is on key pictures only), and the last step is at least ``min_interval_s`` ago. One slow viewer
    among several never steps the encode down.

    **Up:** for a whole quiet period no viewer had an overflow and the longest any picture waited stayed below
    ``headroom`` of the lag limit (the path has room, not merely no trouble yet), and the last step is at least that
    long ago. Hysteresis: a step down soon after a step up says the path did not have the room, and the quiet period
    doubles (up to ``max_backoff`` times) until the path has been steady for a good while.
    """

    def __init__(
        self,
        *,
        min_interval_s: float = 30.0,
        quiet_s: float = 60.0,
        window_s: float = 20.0,
        min_events: int = 2,
        headroom: float = 0.25,
        max_backoff: int = 3,
    ) -> None:
        """Set the intervals, the window the viewers' trouble is counted over, and how much trouble is too much."""
        self.min_interval_s = min_interval_s
        self.quiet_s = quiet_s
        self.window_s = window_s
        self.min_events = min_events
        self.headroom = headroom
        self.max_backoff = max_backoff
        self.changed_at = float("-inf")
        self.stepped_up_at: float | None = None
        self.failures = 0

    def recent_window_s(self, now: float) -> float:
        """How far back the viewers' trouble counts: ``window_s``, but never before the last step (trouble at the old
        quality is not the new one's)."""
        return max(0.001, min(self.window_s, now - self.changed_at))

    @property
    def quiet_needed_s(self) -> float:
        """How long a quiet path is waited for before a step up (longer after each step up that did not hold)."""
        return self.quiet_s * (2**self.failures)

    def stepped(self, now: float, direction: str) -> None:
        """Say that a step was taken (by this adapter's decision or the driver's own), to keep the intervals and the
        back-off."""
        if (
            direction == "down"
            and self.stepped_up_at is not None
            and now - self.stepped_up_at < 2 * self.quiet_needed_s
        ):
            self.failures = min(self.failures + 1, self.max_backoff)
        if direction == "up":
            self.stepped_up_at = now
        self.changed_at = now

    def reset(self) -> None:
        """A new stream: no history."""
        self.changed_at = float("-inf")
        self.stepped_up_at = None
        self.failures = 0

    def is_slow(self, sample: PathSample) -> bool:
        """Whether this viewer's path is not keeping up."""
        return sample.keyframes_only or sample.events >= self.min_events

    def decide(
        self, now: float, *, level: int, top: int, recent: list[PathSample], quiet: list[PathSample]
    ) -> Decision | None:
        """``recent``: the viewers' samples over ``window_s``; ``quiet``: over :attr:`quiet_needed_s`. ``level`` and
        ``top`` are rung indices."""
        if not recent:
            return None
        if self.failures and self.stepped_up_at is not None and now - self.stepped_up_at > 4 * self.quiet_needed_s:
            self.failures = 0  # steady for long enough: the path is not the one that failed
        slow = [s for s in recent if self.is_slow(s)]
        if level > 0 and len(slow) * 2 > len(recent) and now - self.changed_at >= self.min_interval_s:
            who = "the viewer is" if len(recent) == 1 else f"{len(slow)} of {len(recent)} viewers are"
            worst = max(s.events for s in slow)
            return Decision(
                "down",
                f"{who} not keeping up: its queue overflowed {worst} times in {self.window_s:g} s",
            )
        room = level < top and now - self.changed_at >= max(self.min_interval_s, self.quiet_needed_s)
        if room and all(not s.events and not s.keyframes_only and s.peak < self.headroom for s in quiet):
            return Decision(
                "up",
                f"the path has had room for {self.quiet_needed_s:g} s (queues under {self.headroom:.0%} of the limit)",
            )
        return None

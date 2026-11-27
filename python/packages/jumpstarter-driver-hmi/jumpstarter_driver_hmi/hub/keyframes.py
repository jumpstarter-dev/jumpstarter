"""When to ask a source for a key picture: coalesced and rate limited, yet never at one viewer's expense.

Pure code: no clock of its own (the caller says what time it is), no locks (the hub calls it under its own), no source.
It only answers "send the request now?" and counts what it decided; the hub does the sending.

**Why there is a policy at all.** A source for which a key picture is *cheap* (it forces one without restarting
anything) can be asked whenever a viewer needs a start, but the device behind it is not to be flooded: some were seen to
stall when key pictures were forced more often than about once in two seconds. So a request is not repeated inside
``min_interval_s``. That alone would make a viewer wait for what was asked for another, which is what this gate is
careful not to do:

* Inside the window, with a key picture still on its way (asked, not come): nothing is asked again. The one on its way
  serves this viewer too, and if it never comes the request is made again when the window ends (:meth:`poll`).
* Inside the window, the last request answered: a *different* viewer is not that request repeated, so it is sent at once
  (the source paces what reaches the device itself, and merges a request that comes too soon into one sent when its own
  window ends, which is never a request dropped). The *same* viewer asking again is the request repeated, and waits for
  the window to end (it is held, then sent by :meth:`poll`).
* Outside the window: sent.

So no joiner waits for someone else's key picture, and one viewer that keeps on asking costs the device no more than one
request in a window.
"""

from __future__ import annotations


class KeyframeGate:
    """The policy for key-picture requests (see the module documentation); its counters are for ``status()``."""

    def __init__(self, min_interval_s: float = 2.0, retry_after_s: float = 1.0) -> None:
        """Do not repeat a request inside ``min_interval_s``; ask again after ``retry_after_s`` at the soonest when one
        went unanswered."""
        self.min_interval_s = min_interval_s
        self.retry_after_s = retry_after_s
        #: When a request was last sent, and for whom (``None``: several, or nobody in particular).
        self._last_sent: float | None = None
        self._last_sent_for: int | None = None
        #: A key picture has come since the last request was sent (so it was answered).
        self._answered = True
        #: A request is held back until its window ends.
        self._pending = False
        # counters
        self.asked = 0
        self.coalesced = 0
        self.cancelled = 0
        self.shared = 0
        self.forwarded = 0
        self.retries = 0

    @property
    def pending(self) -> bool:
        """Whether a request is being held until its window ends."""
        return self._pending

    @property
    def unanswered(self) -> bool:
        """Whether the last request sent has not been answered by a key picture yet."""
        return not self._answered

    def request(self, now: float, viewer: int | None = None) -> bool:
        """A viewer needs a key picture: whether the request is to be sent now.

        ``viewer`` is the id of the one it is for (``None`` or 0: several, or nobody in particular, which counts as the
        same viewer asking again).
        """
        if self._last_sent is not None and now - self._last_sent < self.min_interval_s:
            if not self._answered:
                self.shared += 1
                return False
            if not viewer or viewer == self._last_sent_for:
                self._pending = True
                self.coalesced += 1
                return False
            self.forwarded += 1
        self.send(now, viewer)
        return True

    def send(self, now: float, viewer: int | None = None) -> None:
        """A request is being sent now (ungated: for a source that must be asked whatever the window, and what
        :meth:`request` ends in)."""
        self._last_sent, self._last_sent_for = now, viewer or None
        self._pending, self._answered = False, False
        self.asked += 1

    def key_came(self) -> None:
        """A key picture came: the last request, whoever made it, is answered."""
        self._answered = True

    def poll(self, now: float, waiting: bool) -> bool:
        """Whether a request is to be sent now that was held for its window, or has gone unanswered through a whole
        window.

        ``waiting``: some viewer still needs a key picture. A held request nobody waits for any more (a key picture came
        meanwhile) is dropped. Called with every unit and from the driver's housekeeping (a stream that has gone quiet).
        """
        since = None if self._last_sent is None else now - self._last_sent
        if self._pending:
            if since is not None and since < self.min_interval_s:
                return False
            self._pending = False
            if not waiting:
                self.cancelled += 1
                return False
        elif (
            waiting
            and not self._answered
            and since is not None
            and since >= max(self.min_interval_s, self.retry_after_s)
        ):
            self.retries += 1  # asked, and a whole window has gone by without the key picture
        else:
            return False
        self.send(now, None)
        return True

    def reset(self) -> None:
        """A new source: what was asked of the old one is not held against the new one."""
        self._last_sent = self._last_sent_for = None
        self._pending, self._answered = False, True

    def stats(self) -> dict:
        """The counters: what was sent, held, shared with a request on its way, forwarded for another viewer, dropped,
        retried."""
        return {
            "asked": self.asked,
            "coalesced": self.coalesced,
            "cancelled": self.cancelled,
            "shared": self.shared,
            "forwarded": self.forwarded,
            "retries": self.retries,
        }

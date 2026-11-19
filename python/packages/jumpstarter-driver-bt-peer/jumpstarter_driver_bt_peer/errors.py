"""The driver's error type, in its own module so every layer can raise it.

a2dp, avrcp, player and driver all need it, and none of them may import each
other freely; a leaf module keeps that acyclic.
"""


class BtPeerError(Exception):
    pass


class TrackRejected(BtPeerError):
    """The track does not match what the sink was offered or agreed to.

    Its own type because it is a verdict on the track, not a failure of the
    link: a queued track the sink rejects is dropped, while one whose stream
    merely would not start stays queued.
    """

"""The driver's error type, in its own module so every layer can raise it.

a2dp, avrcp, player and driver all need it, and none of them may import each
other freely; a leaf module keeps that acyclic.
"""


class BtPeerError(Exception):
    pass

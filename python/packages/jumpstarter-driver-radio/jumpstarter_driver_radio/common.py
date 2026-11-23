from typing import Literal

from pydantic import BaseModel


class RadioPort(BaseModel):
    """A connection point of a radio, in the terms of JEP-0015's ports.

    ``provides``: something listens here and a far end may dial it. ``requires``: this side dials, and a far
    end has to be delivered to it. ``connects``: neither side leads, and two such ports form one path.
    """

    name: str
    direction: Literal["provides", "requires", "connects"]
    # What crosses the port, for whoever joins two of them: only ports that speak the same may be joined.
    protocol: str
    # stream: a byte stream (TCP). datagram and frame are named by JEP-0015 and not carried by its forwards yet.
    transport: Literal["stream", "datagram", "frame"] = "stream"
    # socket: reachable as a socket on the exporter. wired and wireless are physical, and cannot be forwarded.
    attachment: Literal["socket", "wired", "wireless"] = "socket"
    # The child driver through which the port is reached over a lease, until forwards join ports themselves.
    child: str | None = None

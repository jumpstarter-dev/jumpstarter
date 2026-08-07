from pydantic import BaseModel


class VideoState(BaseModel):
    """State common to every video source.

    Source-specific state models extend this, so a client can read the common
    fields from any video driver while richer sources keep their own detail.
    """

    online: bool = False
    """whether the source is currently producing frames"""

    width: int | None = None
    """frame width in pixels, if known"""

    height: int | None = None
    """frame height in pixels, if known"""

    fps: float | None = None
    """frames per second currently captured, if the source reports it"""

from jumpstarter_driver_video.common import VideoState
from pydantic import BaseModel, model_validator


class UStreamerState(VideoState):
    class Result(BaseModel):
        class Encoder(BaseModel):
            type: str
            """type of encoder in use, e.g. CPU/GPU"""
            quality: int
            """encoding quality"""

        class Source(BaseModel):
            class Resolution(BaseModel):
                width: int
                """resolution width"""
                height: int
                """resolution height"""

            online: bool
            """client active"""
            desired_fps: int
            """desired fps"""
            captured_fps: int
            """actual fps"""

            resolution: Resolution

        encoder: Encoder
        source: Source

    ok: bool

    result: Result

    @model_validator(mode="after")
    def _fill_common_state(self):
        """Populate the common VideoState fields from ustreamer's own shape."""
        self.online = self.result.source.online
        self.width = self.result.source.resolution.width
        self.height = self.result.source.resolution.height
        self.fps = self.result.source.captured_fps
        return self

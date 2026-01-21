from pydantic import BaseModel


class UStreamerState(BaseModel):
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

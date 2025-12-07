from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DecoderConfig(BaseModel):
    """Protocol decoder configuration (real-time during capture)."""

    name: str
    channels: dict[str, str] | None = None
    options: dict[str, Any] | None = None
    annotations: list[str] | None = None
    stack: list["DecoderConfig"] | None = None


class CaptureConfig(BaseModel):
    sample_rate: str = Field(default="1M", description="e.g., 8MHz, 1M, 24000000")
    samples: int | None = Field(default=None, description="number of samples; None for continuous")
    pretrigger: int | None = Field(default=None, description="samples before trigger")
    triggers: dict[str, str] | None = Field(default=None, description="e.g., {'D0': 'rising'}")
    channels: list[str] | None = Field(default=None, description="override default channels by name")
    output_format: str = Field(
        default="srzip",
        description="csv, srzip, vcd, binary, bits, ascii",
    )
    decoders: list[DecoderConfig] | None = Field(default=None, description="real-time protocol decoding")


class CaptureResult(BaseModel):
    """Result from a capture operation.

    Note: data is base64-encoded for reliable JSON transport. Client methods
    automatically decode it to bytes for you.
    """
    data_b64: str  # Base64-encoded binary data
    output_format: str
    sample_rate: str
    channel_map: dict[str, str]
    triggers: dict[str, str] | None = None
    decoders: list[DecoderConfig] | None = None

    @property
    def data(self) -> bytes:
        """Get the captured data as bytes (auto-decodes from base64)."""
        from base64 import b64decode
        return b64decode(self.data_b64)


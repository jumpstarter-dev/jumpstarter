from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OutputFormat:
    """Constants for sigrok output formats."""
    CSV = "csv"
    BITS = "bits"
    ASCII = "ascii"
    BINARY = "binary"
    SRZIP = "srzip"
    VCD = "vcd"

    @classmethod
    def all(cls) -> list[str]:
        return [cls.CSV, cls.BITS, cls.ASCII, cls.BINARY, cls.SRZIP, cls.VCD]


class Sample(BaseModel):
    """A single sample with timing information."""
    sample: int  # Sample index
    time_ns: int  # Time in nanoseconds
    values: dict[str, int | float]  # Channel values (digital: 0/1, analog: voltage)


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

    def decode(self) -> list[Sample] | dict[str, list[int]] | str:
        """Parse captured data based on output format.

        Returns:
            - CSV format: list[Sample] with timing and all values per sample
            - VCD format: list[Sample] with timing and only changed values
            - Bits format: dict[str, list[int]] with channel→bit sequences
            - ASCII format: str with ASCII art visualization
            - Other formats: raises NotImplementedError (use .data for raw bytes)

        Raises:
            NotImplementedError: For binary/srzip formats (use .data property)
        """
        if self.output_format == OutputFormat.CSV:
            from .csv import parse_csv
            samples_data = parse_csv(self.data, self.sample_rate)
            return [Sample.model_validate(s) for s in samples_data]
        elif self.output_format == OutputFormat.VCD:
            from .vcd import parse_vcd
            samples_data = parse_vcd(self.data, self.sample_rate)
            return [Sample.model_validate(s) for s in samples_data]
        elif self.output_format == OutputFormat.BITS:
            return self._parse_bits()
        elif self.output_format == OutputFormat.ASCII:
            return self.data.decode("utf-8")
        else:
            raise NotImplementedError(
                f"Parsing not implemented for {self.output_format} format. "
                f"Use .data property to get raw bytes."
            )

    def _parse_bits(self) -> dict[str, list[int]]:
        """Parse bits format to dict of channel→bit sequences."""
        text = self.data.decode("utf-8")
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]

        # bits format is just columns of 0/1
        # TODO: Need to determine channel mapping from somewhere
        # For now, return as generic numbered channels
        result: dict[str, list[int]] = {}

        for line in lines:
            # Each line might be space/comma separated bits
            bits = [int(b) for b in line if b in "01"]
            if not result:
                # Initialize channels
                for i, bit in enumerate(bits):
                    result[f"CH{i}"] = [bit]
            else:
                # Append to existing channels
                for i, bit in enumerate(bits):
                    if f"CH{i}" in result:
                        result[f"CH{i}"].append(bit)

        return result


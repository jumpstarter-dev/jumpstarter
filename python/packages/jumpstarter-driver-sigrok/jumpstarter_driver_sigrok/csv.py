"""CSV format parser for sigrok captures."""

from __future__ import annotations

import csv
from typing import Iterator


def parse_csv(data: bytes, sample_rate: str) -> Iterator[dict]:
    """Parse CSV format to iterator of samples with timing.

    Args:
        data: Raw CSV data as bytes
        sample_rate: Sample rate string (e.g., "100kHz", "1MHz")

    Yields:
        Dicts with keys: sample, time_ns, values
    """
    text = data.decode("utf-8")
    lines = text.strip().split("\n")

    # Parse sample rate for timing calculation
    sample_rate_hz = _parse_sample_rate_hz(sample_rate)
    time_step_ns = int(1_000_000_000.0 / sample_rate_hz)

    # Skip comment lines and analog preview lines (format: "A0: -10.0000 V DC")
    # The actual data starts after a header row with types like "logic,logic,V DC,V DC"
    data_lines = _extract_csv_data_lines(lines)

    if not data_lines or len(data_lines) < 2:
        return

    # Parse the CSV data
    reader = csv.reader(data_lines)

    # First row is types (logic, V DC, etc.) - use for channel name inference
    types_row = next(reader)

    # Get channel names from types
    channel_names = _infer_channel_names(types_row)

    # Parse and yield data rows one by one
    for idx, row in enumerate(reader):
        values = _parse_csv_row(channel_names, row)
        yield {
            "sample": idx,
            "time_ns": idx * time_step_ns,
            "values": values,
        }


def _parse_sample_rate_hz(sample_rate: str) -> float:
    """Parse sample rate string to Hz."""
    rate = sample_rate.strip().upper()
    multipliers = {"K": 1e3, "M": 1e6, "G": 1e9}

    for suffix, mult in multipliers.items():
        if rate.endswith(f"{suffix}HZ"):
            return float(rate[:-3]) * mult
        elif rate.endswith(suffix):
            return float(rate[:-1]) * mult

    # Assume Hz if no suffix
    return float(rate.rstrip("HZ"))


def _extract_csv_data_lines(lines: list[str]) -> list[str]:
    """Extract actual CSV data lines, skipping comments and analog preview lines."""
    data_lines = []

    for _i, line in enumerate(lines):
        line = line.strip()
        # Skip comment lines
        if line.startswith(";"):
            continue
        # Skip analog preview lines (contain colon, not CSV comma-separated)
        if ":" in line and "," not in line:
            continue
        # This is CSV data
        data_lines.append(line)

    return data_lines


def _infer_channel_names(types_row: list[str]) -> list[str]:
    """Infer channel names from CSV type header row.

    Args:
        types_row: List of type strings like ["logic", "logic", "V DC", "V DC"]

    Returns:
        List of channel names like ["D0", "D1", "A0", "A1"]
    """
    channel_names = []
    digital_count = 0
    analog_count = 0

    for type_str in types_row:
        type_lower = type_str.lower()
        if "logic" in type_lower:
            channel_names.append(f"D{digital_count}")
            digital_count += 1
        elif "v" in type_lower or "dc" in type_lower:
            # Analog channel
            channel_names.append(f"A{analog_count}")
            analog_count += 1
        else:
            # Unknown type, use generic name
            channel_names.append(f"CH{len(channel_names)}")

    return channel_names


def _parse_csv_row(channel_names: list[str], row: list[str]) -> dict[str, int | float]:
    """Parse a CSV data row into channel values.

    Args:
        channel_names: List of channel names
        row: List of value strings

    Returns:
        Dict mapping channel name to parsed value
    """
    values = {}

    for channel, value in zip(channel_names, row, strict=True):
        value = value.strip()
        # Try to parse as number (analog) or binary (digital)
        try:
            if "." in value or "e" in value.lower():
                values[channel] = float(value)
            else:
                values[channel] = int(value)
        except ValueError:
            # Keep as string if not a number
            values[channel] = value

    return values


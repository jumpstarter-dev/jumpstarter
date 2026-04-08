"""VCD (Value Change Dump) format parser for sigrok captures."""

from __future__ import annotations

import logging
from typing import Iterator

logger = logging.getLogger(__name__)


def parse_vcd(data: bytes, sample_rate: str) -> Iterator[dict]:
    """Parse VCD format to iterator of samples with timing (changes only).

    VCD format only records when signals change, making it efficient for
    sparse data. Each sample represents a time point where one or more
    signals changed.

    Args:
        data: Raw VCD data as bytes
        sample_rate: Sample rate string (not used for VCD as it has its own timescale)

    Yields:
        Dicts with keys: sample, time (seconds), values
    """
    text = data.decode("utf-8")
    lines = text.strip().split("\n")

    timescale_multiplier, channel_map = _parse_vcd_header(lines)
    yield from _parse_vcd_body(lines, timescale_multiplier, channel_map)


def _parse_vcd_header(lines: list[str]) -> tuple[float, dict[str, str]]:
    """Parse VCD header to extract timescale and channel mapping."""
    timescale_multiplier = 1e-9  # Default: 1 unit = 1 ns = 1e-9 seconds
    channel_map: dict[str, str] = {}  # symbol -> channel name

    for line in lines:
        line = line.strip()

        if line.startswith("$timescale"):
            timescale_multiplier = _parse_timescale(line)

        if line.startswith("$var"):
            parts = line.split()
            if len(parts) >= 5:
                symbol = parts[3]
                channel = parts[4]
                channel_map[symbol] = channel

        if line == "$enddefinitions $end":
            break

    return timescale_multiplier, channel_map


def _parse_vcd_body(
    lines: list[str],
    timescale_multiplier: float,
    channel_map: dict[str, str],
) -> Iterator[dict]:
    """Parse VCD body, yielding samples for each timestamp block."""
    sample_idx = 0
    current_time_s: float | None = None
    current_values: dict[str, int | float] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("$"):
            if line.startswith("$dumpvars") and current_time_s is None:
                current_time_s = 0.0
            continue

        if line.startswith("#"):
            # Flush previous timestamp block
            if current_time_s is not None and current_values:
                yield {"sample": sample_idx, "time": current_time_s, "values": current_values}
                sample_idx += 1
                current_values = {}

            current_time_s = _parse_timestamp(line, timescale_multiplier)

            # Inline values on the same line (if present)
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                _parse_vcd_value_changes(parts[1], channel_map, current_values)
            continue

        # Value change line (may appear after # or inside $dumpvars)
        if current_time_s is None:
            current_time_s = 0.0
        _parse_vcd_value_changes(line, channel_map, current_values)

    # Flush final block
    if current_time_s is not None and current_values:
        yield {"sample": sample_idx, "time": current_time_s, "values": current_values}


def _parse_timestamp(line: str, timescale_multiplier: float) -> float:
    """Parse a VCD timestamp line (e.g., '#100') and return time in seconds."""
    time_str = line.split(maxsplit=1)[0][1:]  # Remove '#' prefix
    if time_str:
        return int(time_str) * timescale_multiplier
    return 0.0


def _parse_timescale(line: str) -> float:
    """Parse timescale line and return multiplier to convert to seconds."""
    parts = line.split()
    if len(parts) >= 3:
        value = parts[1]
        unit = parts[2]
        # Convert to seconds multiplier
        unit_multipliers = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12, "fs": 1e-15}
        if unit not in unit_multipliers:
            raise ValueError(f"Unknown VCD timescale unit: {unit!r} in line: {line!r}")
        return float(value) * unit_multipliers[unit]
    raise ValueError(f"Cannot parse VCD timescale line: {line!r}. Expected format: '$timescale <value> <unit> $end'")


def _parse_vcd_value_changes(values_str: str, channel_map: dict[str, str], current_values: dict[str, int | float]):
    """Parse value change tokens from a VCD line.

    Modifies current_values dict in place.

    Supports:
    - Single-bit: "1!", "0abc"
    - Binary: "b11110000 abc"
    - Real: "r3.14159 xyz", "r-10.5 !", "r1.23e-5 aa"
    """
    i = 0
    while i < len(values_str):
        char = values_str[i]

        # Single bit change (e.g., "1!", "0abc" for multi-char identifiers)
        if char in "01xzXZ":
            symbol, new_i = _extract_symbol(values_str, i + 1)
            if symbol in channel_map:
                channel = channel_map[symbol]
                if char in "xzXZ":
                    logger.warning("VCD channel %s has %s state, mapping to 0", channel, char)
                current_values[channel] = 1 if char == "1" else 0
            i = new_i

        # Binary value (e.g., "b1010 !" or "b1010 abc")
        elif char == "b":
            value, symbol, new_i = _parse_binary_value(values_str, i, channel_map)
            if symbol and value is not None:
                current_values[channel_map[symbol]] = value
            i = new_i

        # Real (analog) value (e.g., "r3.14 !" or "r-10.5 abc")
        elif char == "r":
            value, symbol, new_i = _parse_real_value(values_str, i, channel_map)
            if symbol and value is not None:
                current_values[channel_map[symbol]] = value
            i = new_i

        # Skip whitespace
        elif char == " ":
            i += 1
        else:
            i += 1


def _extract_symbol(text: str, start: int) -> tuple[str, int]:
    """Extract a VCD symbol (can be multi-character) from text.

    Returns:
        Tuple of (symbol, next_position)
    """
    end = start
    while end < len(text) and text[end] != " ":
        end += 1
    return text[start:end], end


def _parse_binary_value(values_str: str, start: int, channel_map: dict[str, str]) -> tuple[int | None, str | None, int]:
    """Parse a binary value like "b1010 abc".

    Returns:
        Tuple of (value, symbol, next_position)
    """
    # Extract binary value
    value_start = start + 1
    value_end = value_start
    while value_end < len(values_str) and values_str[value_end] in "01xzXZ":
        value_end += 1
    binary_value = values_str[value_start:value_end]

    # Skip whitespace before symbol
    while value_end < len(values_str) and values_str[value_end] == " ":
        value_end += 1

    # Extract symbol
    symbol, next_pos = _extract_symbol(values_str, value_end)

    if symbol in channel_map:
        try:
            return int(binary_value, 2), symbol, next_pos
        except ValueError:
            return 0, symbol, next_pos

    return None, None, next_pos


def _parse_real_value(values_str: str, start: int, channel_map: dict[str, str]) -> tuple[float | None, str | None, int]:
    """Parse a real (analog) value like "r3.14 abc" or "r-10.5 !".

    Returns:
        Tuple of (value, symbol, next_position)
    """
    # Extract real value (number with optional sign, decimal, exponent)
    value_start = start + 1
    value_end = value_start
    while value_end < len(values_str) and values_str[value_end] not in " ":
        if values_str[value_end] in "0123456789-.eE+":
            value_end += 1
        else:
            break
    real_value = values_str[value_start:value_end]

    # Skip whitespace before symbol
    while value_end < len(values_str) and values_str[value_end] == " ":
        value_end += 1

    # Extract symbol
    symbol, next_pos = _extract_symbol(values_str, value_end)

    if symbol in channel_map:
        try:
            return float(real_value), symbol, next_pos
        except ValueError:
            return 0.0, symbol, next_pos

    return None, None, next_pos


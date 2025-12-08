"""VCD (Value Change Dump) format parser for sigrok captures."""

from __future__ import annotations


def parse_vcd(data: bytes, sample_rate: str) -> list[dict]:
    """Parse VCD format to list of samples with timing (changes only).

    VCD format only records when signals change, making it efficient for
    sparse data. Each sample represents a time point where one or more
    signals changed.

    Args:
        data: Raw VCD data as bytes
        sample_rate: Sample rate string (not used for VCD as it has its own timescale)

    Returns:
        List of dicts with keys: sample, time_ns, values
    """
    text = data.decode("utf-8")
    lines = text.strip().split("\n")

    # Parse VCD header to extract timescale and channel mapping
    timescale_multiplier = 1  # Default: 1 unit = 1 ns
    channel_map: dict[str, str] = {}  # symbol → channel name

    for line in lines:
        line = line.strip()

        # Parse timescale (e.g., "$timescale 1 us $end" means 1 unit = 1000 ns)
        if line.startswith("$timescale"):
            timescale_multiplier = _parse_timescale(line)

        # Parse variable definitions (e.g., "$var wire 1 ! D0 $end")
        if line.startswith("$var"):
            parts = line.split()
            if len(parts) >= 5:
                symbol = parts[3]  # e.g., "!"
                channel = parts[4]  # e.g., "D0"
                channel_map[symbol] = channel

        if line == "$enddefinitions $end":
            break

    # Parse value changes
    samples: list[dict] = []
    sample_idx = 0

    for line in lines:
        line = line.strip()
        if not line or line.startswith("$"):
            continue

        # Timestamp line (e.g., "#100 1! 0" 1#")
        if line.startswith("#"):
            sample_data = _parse_vcd_timestamp_line(line, timescale_multiplier, channel_map)
            if sample_data is not None:
                sample_data["sample"] = sample_idx
                samples.append(sample_data)
                sample_idx += 1

    return samples


def _parse_timescale(line: str) -> int:
    """Parse timescale line and return multiplier to convert to nanoseconds."""
    parts = line.split()
    if len(parts) >= 3:
        value = parts[1]
        unit = parts[2]
        # Convert to nanoseconds multiplier
        unit_multipliers = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1, "ps": 1e-3}
        return int(float(value) * unit_multipliers.get(unit, 1))
    return 1


def _parse_vcd_timestamp_line(line: str, timescale_multiplier: int, channel_map: dict[str, str]) -> dict | None:
    """Parse a VCD timestamp line with value changes.

    Args:
        line: Line starting with # (e.g., "#100 1! 0" 1#")
        timescale_multiplier: Multiplier to convert time units to nanoseconds
        channel_map: Mapping from VCD symbols to channel names

    Returns:
        Dict with time_ns and values, or None if line is empty
    """
    # Split timestamp from values
    parts = line.split(maxsplit=1)
    time_str = parts[0][1:]  # Remove '#' prefix

    # Skip empty time lines
    if not time_str:
        return None

    time_units = int(time_str)
    current_time_ns = time_units * timescale_multiplier
    current_values: dict[str, int | float] = {}

    # Parse value changes if present on the same line
    if len(parts) > 1:
        values_str = parts[1]
        _parse_vcd_value_changes(values_str, channel_map, current_values)

    # Return sample data if we have values
    if current_values:
        return {"time_ns": current_time_ns, "values": current_values}

    return None


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


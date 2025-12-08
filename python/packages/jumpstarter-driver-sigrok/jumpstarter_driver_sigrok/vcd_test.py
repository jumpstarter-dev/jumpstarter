"""Tests for VCD (Value Change Dump) format parser."""

from base64 import b64encode

from .common import CaptureResult, OutputFormat, Sample


def test_vcd_parser_comprehensive():
    """Test VCD parser with manually constructed VCD data covering all features.

    This test validates:
    - Single-character identifiers (!, ", #)
    - Multi-character identifiers (aa, ab, abc)
    - Timescale parsing (microseconds to nanoseconds)
    - Single-bit values (0/1)
    - X/Z state handling
    - Binary values (vectors)
    - Real (analog) values with various formats
    """
    # Construct a comprehensive VCD file
    vcd_content = """$date Mon Dec  8 2025 $end
$version Test VCD Generator $end
$timescale 1 us $end
$scope module test $end
$var wire 1 ! D0 $end
$var wire 1 " D1 $end
$var wire 1 # D2 $end
$var wire 1 aa CH95 $end
$var wire 1 ab CH96 $end
$var wire 8 abc BUS0 $end
$var real 1 xyz ANALOG0 $end
$upscope $end
$enddefinitions $end
#0 1! 0" 1# 0aa 1ab b00001111 abc r-10.5 xyz
#5 0! 1" x# 1aa
#10 z! 0" 1# b11110000 abc r3.14159 xyz
#25 1! 1" 0# 0aa 0ab b10101010 abc r0.0 xyz
#100 0! 0" 0# r1.23e-5 xyz
"""

    # Create a CaptureResult with this VCD data
    result = CaptureResult(
        data_b64=b64encode(vcd_content.encode("utf-8")).decode("ascii"),
        output_format=OutputFormat.VCD,
        sample_rate="1MHz",
        channel_map={
            "D0": "d0",
            "D1": "d1",
            "D2": "d2",
            "CH95": "ch95",
            "CH96": "ch96",
            "BUS0": "bus",
            "ANALOG0": "analog",
        },
        triggers=None,
        decoders=None,
    )

    # Parse the VCD
    samples = result.decode()

    # Verify we got the expected number of samples
    assert len(samples) == 5

    # Sample 0 at time 0us = 0ns
    s0 = samples[0]
    assert s0.time_ns == 0
    assert s0.values["D0"] == 1
    assert s0.values["D1"] == 0
    assert s0.values["D2"] == 1
    assert s0.values["CH95"] == 0  # Multi-char identifier "aa"
    assert s0.values["CH96"] == 1  # Multi-char identifier "ab"
    assert s0.values["BUS0"] == 0b00001111  # Binary value
    assert abs(s0.values["ANALOG0"] - (-10.5)) < 0.001  # Real value

    # Sample 1 at time 5us = 5000ns
    s1 = samples[1]
    assert s1.time_ns == 5000
    assert s1.values["D0"] == 0
    assert s1.values["D1"] == 1
    assert s1.values["D2"] == 0  # X converted to 0
    assert s1.values["CH95"] == 1

    # Sample 2 at time 10us = 10000ns
    s2 = samples[2]
    assert s2.time_ns == 10000
    assert s2.values["D0"] == 0  # Z converted to 0
    assert s2.values["D1"] == 0
    assert s2.values["D2"] == 1
    assert s2.values["BUS0"] == 0b11110000
    assert abs(s2.values["ANALOG0"] - 3.14159) < 0.001

    # Sample 3 at time 25us = 25000ns
    s3 = samples[3]
    assert s3.time_ns == 25000
    assert s3.values["D0"] == 1
    assert s3.values["D1"] == 1
    assert s3.values["D2"] == 0
    assert s3.values["CH95"] == 0
    assert s3.values["CH96"] == 0
    assert s3.values["BUS0"] == 0b10101010
    assert abs(s3.values["ANALOG0"] - 0.0) < 0.001

    # Sample 4 at time 100us = 100000ns
    s4 = samples[4]
    assert s4.time_ns == 100000
    assert s4.values["D0"] == 0
    assert s4.values["D1"] == 0
    assert s4.values["D2"] == 0
    assert abs(s4.values["ANALOG0"] - 1.23e-5) < 1e-10  # Scientific notation


def test_vcd_parser_timescale_variations():
    """Test VCD parser with different timescale values."""
    # Test different timescales
    test_cases = [
        ("1 ns", 1, 0),      # 1ns timescale, time 0 = 0ns
        ("1 us", 1000, 0),   # 1us timescale, time 0 = 0ns
        ("1 ms", 1000000, 0),  # 1ms timescale, time 0 = 0ns
        ("10 ns", 10, 100 * 10),  # 10ns timescale, time 100 = 1000ns
        ("100 ns", 100, 50 * 100),  # 100ns timescale, time 50 = 5000ns
    ]

    for timescale_str, _multiplier, expected_time_ns in test_cases:
        vcd_content = f"""$timescale {timescale_str} $end
$var wire 1 ! D0 $end
$enddefinitions $end
#0 1!
#{100 if expected_time_ns else 0} 0!
"""
        result = CaptureResult(
            data_b64=b64encode(vcd_content.encode("utf-8")).decode("ascii"),
            output_format=OutputFormat.VCD,
            sample_rate="1MHz",
            channel_map={"D0": "d0"},
        )

        samples = result.decode()
        assert len(samples) >= 1
        # First sample at time 0
        assert samples[0].time_ns == 0


def test_vcd_parser_empty_timestamps():
    """Test VCD parser handles empty timestamp lines correctly."""
    vcd_content = """$timescale 1 ns $end
$var wire 1 ! D0 $end
$enddefinitions $end
#0 1!
#10 0!
#
#20 1!
"""

    result = CaptureResult(
        data_b64=b64encode(vcd_content.encode("utf-8")).decode("ascii"),
        output_format=OutputFormat.VCD,
        sample_rate="1MHz",
        channel_map={"D0": "d0"},
    )

    samples = result.decode()
    # Should have 3 samples (empty timestamp line skipped)
    assert len(samples) == 3
    assert samples[0].time_ns == 0
    assert samples[1].time_ns == 10
    assert samples[2].time_ns == 20


def test_vcd_parser_large_channel_count():
    """Test VCD parser with large channel counts using multi-char identifiers.

    According to libsigrok vcd_identifier():
    - Channels 0-93: Single char (!, ", ..., ~)
    - Channels 94-769: Two lowercase letters (aa, ab, ..., zz)
    - Channels 770+: Three lowercase letters (aaa, aab, ...)
    """
    # Test identifiers at boundaries
    vcd_content = """$timescale 1 ns $end
$var wire 1 ! CH0 $end
$var wire 1 ~ CH93 $end
$var wire 1 aa CH94 $end
$var wire 1 ab CH95 $end
$var wire 1 zz CH769 $end
$var wire 1 aaa CH770 $end
$var wire 1 abc CH800 $end
$enddefinitions $end
#0 1! 0~ 1aa 0ab 1zz 0aaa 1abc
#100 0! 1~ 0aa 1ab 0zz 1aaa 0abc
"""

    result = CaptureResult(
        data_b64=b64encode(vcd_content.encode("utf-8")).decode("ascii"),
        output_format=OutputFormat.VCD,
        sample_rate="1MHz",
        channel_map={
            "CH0": "ch0",
            "CH93": "ch93",
            "CH94": "ch94",
            "CH95": "ch95",
            "CH769": "ch769",
            "CH770": "ch770",
            "CH800": "ch800",
        },
    )

    samples = result.decode()

    # Verify first sample
    assert len(samples) == 2
    s0 = samples[0]
    assert isinstance(s0, Sample)
    assert s0.time_ns == 0
    assert s0.values["CH0"] == 1  # Single char: !
    assert s0.values["CH93"] == 0  # Single char: ~
    assert s0.values["CH94"] == 1  # Two char: aa
    assert s0.values["CH95"] == 0  # Two char: ab
    assert s0.values["CH769"] == 1  # Two char: zz
    assert s0.values["CH770"] == 0  # Three char: aaa
    assert s0.values["CH800"] == 1  # Three char: abc

    # Verify second sample
    s1 = samples[1]
    assert s1.time_ns == 100
    assert s1.values["CH0"] == 0
    assert s1.values["CH93"] == 1
    assert s1.values["CH94"] == 0
    assert s1.values["CH95"] == 1
    assert s1.values["CH769"] == 0
    assert s1.values["CH770"] == 1
    assert s1.values["CH800"] == 0


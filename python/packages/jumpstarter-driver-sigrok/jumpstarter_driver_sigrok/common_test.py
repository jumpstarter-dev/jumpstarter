"""Tests for common.py models and utilities."""

from base64 import b64encode

from .common import CaptureResult, OutputFormat, Sample


class TestSampleFormatTime:
    """Tests for Sample._format_time() covering all time unit branches."""

    def test_zero_time(self):
        assert Sample._format_time(0) == "0s"

    def test_seconds(self):
        assert Sample._format_time(1.5) == "1.5s"
        assert Sample._format_time(10.0) == "10s"

    def test_milliseconds(self):
        assert Sample._format_time(0.001) == "1ms"
        assert Sample._format_time(0.0025) == "2.5ms"

    def test_microseconds(self):
        assert Sample._format_time(1e-6) == "1us"
        assert Sample._format_time(3.5e-6) == "3.5us"

    def test_nanoseconds(self):
        assert Sample._format_time(1e-9) == "1ns"
        assert Sample._format_time(500e-9) == "500ns"

    def test_picoseconds(self):
        assert Sample._format_time(1e-12) == "1ps"
        assert Sample._format_time(250e-12) == "250ps"

    def test_femtoseconds(self):
        assert Sample._format_time(1e-15) == "1fs"
        assert Sample._format_time(50e-15) == "50fs"

    def test_sub_femtosecond_uses_femtoseconds(self):
        # Values below 1fs should still use fs unit (minimum)
        result = Sample._format_time(0.5e-15)
        assert result == "0.5fs"

    def test_negative_time(self):
        # Negative time should work using absolute value for unit selection
        result = Sample._format_time(-1e-6)
        assert result == "-1us"


class TestSampleStr:
    """Tests for Sample.__str__() formatting."""

    def test_str_formatting(self):
        s = Sample(sample=0, time=1e-6, values={"D0": 1})
        result = str(s)
        assert "Sample(" in result
        assert "sample=0" in result
        assert "1us" in result
        assert "D0" in result


class TestCaptureResultStr:
    """Tests for CaptureResult.__str__() truncation logic."""

    def test_short_data_no_truncation(self):
        short_data = b"hello"
        result = CaptureResult(
            data_b64=b64encode(short_data).decode("ascii"),
            output_format="csv",
            sample_rate="1M",
            channel_map={},
        )
        s = str(result)
        assert "..." not in s
        assert "CaptureResult(" in s

    def test_long_data_truncated(self):
        # Create data long enough that base64 exceeds 50 chars
        long_data = b"x" * 1000
        result = CaptureResult(
            data_b64=b64encode(long_data).decode("ascii"),
            output_format="csv",
            sample_rate="1M",
            channel_map={},
        )
        s = str(result)
        assert "..." in s
        assert "chars)" in s

    def test_str_includes_metadata(self):
        result = CaptureResult(
            data_b64=b64encode(b"test").decode("ascii"),
            output_format="vcd",
            sample_rate="100kHz",
            channel_map={"D0": "clk", "D1": "data"},
        )
        s = str(result)
        assert "vcd" in s
        assert "100kHz" in s
        assert "channels=2" in s


class TestCaptureResultData:
    """Tests for CaptureResult.data property (base64 decoding)."""

    def test_data_decodes_correctly(self):
        original = b"binary data \x00\x01\x02\xff"
        result = CaptureResult(
            data_b64=b64encode(original).decode("ascii"),
            output_format="csv",
            sample_rate="1M",
            channel_map={},
        )
        assert result.data == original

    def test_data_returns_bytes(self):
        result = CaptureResult(
            data_b64=b64encode(b"test").decode("ascii"),
            output_format="csv",
            sample_rate="1M",
            channel_map={},
        )
        assert isinstance(result.data, bytes)

    def test_data_empty(self):
        result = CaptureResult(
            data_b64=b64encode(b"").decode("ascii"),
            output_format="csv",
            sample_rate="1M",
            channel_map={},
        )
        assert result.data == b""


class TestCaptureResultParseBits:
    """Tests for CaptureResult._parse_bits() parsing."""

    def test_basic_bits_parsing(self):
        bits_data = b"D0:10001\nD1:01110\n"
        result = CaptureResult(
            data_b64=b64encode(bits_data).decode("ascii"),
            output_format="bits",
            sample_rate="1M",
            channel_map={},
        )
        decoded = result._parse_bits()
        assert decoded["D0"] == [1, 0, 0, 0, 1]
        assert decoded["D1"] == [0, 1, 1, 1, 0]

    def test_bits_with_channel_map(self):
        bits_data = b"D0:101\nD1:010\n"
        result = CaptureResult(
            data_b64=b64encode(bits_data).decode("ascii"),
            output_format="bits",
            sample_rate="1M",
            channel_map={"D0": "clk", "D1": "data"},
        )
        decoded = result._parse_bits()
        assert "clk" in decoded
        assert "data" in decoded
        assert decoded["clk"] == [1, 0, 1]
        assert decoded["data"] == [0, 1, 0]

    def test_bits_multiline_accumulation(self):
        # Sigrok wraps bits across multiple lines with repeated channel names
        bits_data = b"D0:1010\nD1:0101\nD0:1100\nD1:0011\n"
        result = CaptureResult(
            data_b64=b64encode(bits_data).decode("ascii"),
            output_format="bits",
            sample_rate="1M",
            channel_map={},
        )
        decoded = result._parse_bits()
        assert decoded["D0"] == [1, 0, 1, 0, 1, 1, 0, 0]
        assert decoded["D1"] == [0, 1, 0, 1, 0, 0, 1, 1]

    def test_bits_empty_data(self):
        bits_data = b""
        result = CaptureResult(
            data_b64=b64encode(bits_data).decode("ascii"),
            output_format="bits",
            sample_rate="1M",
            channel_map={},
        )
        decoded = result._parse_bits()
        assert decoded == {}

    def test_bits_via_decode(self):
        """Test that decode() dispatches to _parse_bits for bits format."""
        bits_data = b"D0:101\n"
        result = CaptureResult(
            data_b64=b64encode(bits_data).decode("ascii"),
            output_format="bits",
            sample_rate="1M",
            channel_map={},
        )
        decoded = result.decode()
        assert isinstance(decoded, dict)
        assert decoded["D0"] == [1, 0, 1]


class TestOutputFormatAll:
    """Tests for OutputFormat.all()."""

    def test_all_returns_list_of_strings(self):
        result = OutputFormat.all()
        assert isinstance(result, list)
        assert all(isinstance(v, str) for v in result)

    def test_all_contains_expected_formats(self):
        result = OutputFormat.all()
        assert "csv" in result
        assert "vcd" in result
        assert "bits" in result
        assert "ascii" in result
        assert "binary" in result
        assert "srzip" in result

    def test_all_length_matches_enum_members(self):
        result = OutputFormat.all()
        assert len(result) == len(OutputFormat)

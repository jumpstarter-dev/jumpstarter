"""Tests for CSV format parser."""

from shutil import which

import pytest

from .client import SigrokClient
from .common import CaptureConfig, CaptureResult, OutputFormat
from .csv import parse_csv

# ---------------------------------------------------------------------------
# Unit tests for parse_csv (no sigrok-cli required)
# ---------------------------------------------------------------------------


class TestParseCsvDigitalOnly:
    """Test parse_csv with digital-only CSV data."""

    def test_digital_channels(self):
        csv_data = (
            b"; sigrok-cli output\n"
            b"logic,logic,logic\n"
            b"1,0,1\n"
            b"0,1,0\n"
            b"1,1,1\n"
        )
        samples = list(parse_csv(csv_data, "100kHz"))
        assert len(samples) == 3
        assert samples[0]["values"] == {"D0": 1, "D1": 0, "D2": 1}
        assert samples[1]["values"] == {"D0": 0, "D1": 1, "D2": 0}
        assert samples[2]["values"] == {"D0": 1, "D1": 1, "D2": 1}

    def test_digital_timing(self):
        csv_data = b"logic,logic\n1,0\n0,1\n"
        samples = list(parse_csv(csv_data, "1MHz"))
        assert samples[0]["time"] == pytest.approx(0.0)
        assert samples[1]["time"] == pytest.approx(1e-6)


class TestParseCsvAnalogOnly:
    """Test parse_csv with analog-only CSV data."""

    def test_analog_channels(self):
        csv_data = (
            b"; analog capture\n"
            b"V DC,V DC\n"
            b"3.14,2.71\n"
            b"-1.5,0.0\n"
        )
        samples = list(parse_csv(csv_data, "100kHz"))
        assert len(samples) == 2
        assert samples[0]["values"]["A0"] == pytest.approx(3.14)
        assert samples[0]["values"]["A1"] == pytest.approx(2.71)
        assert samples[1]["values"]["A0"] == pytest.approx(-1.5)
        assert samples[1]["values"]["A1"] == pytest.approx(0.0)


class TestParseCsvMixed:
    """Test parse_csv with mixed digital and analog CSV data."""

    def test_mixed_channels(self):
        csv_data = (
            b"logic,logic,V DC\n"
            b"1,0,3.3\n"
            b"0,1,-1.2\n"
        )
        samples = list(parse_csv(csv_data, "100kHz"))
        assert len(samples) == 2
        assert samples[0]["values"] == {"D0": 1, "D1": 0, "A0": pytest.approx(3.3)}
        assert samples[1]["values"] == {"D0": 0, "D1": 1, "A0": pytest.approx(-1.2)}


class TestParseCsvEmpty:
    """Test parse_csv with empty or minimal data."""

    def test_empty_bytes(self):
        samples = list(parse_csv(b"", "1MHz"))
        assert samples == []

    def test_only_comments(self):
        csv_data = b"; comment line 1\n; comment line 2\n"
        samples = list(parse_csv(csv_data, "1MHz"))
        assert samples == []

    def test_header_only_no_data(self):
        csv_data = b"logic,logic\n"
        samples = list(parse_csv(csv_data, "1MHz"))
        assert samples == []


class TestParseCsvSkipsAnalogPreview:
    """Test that analog preview lines are properly skipped."""

    def test_skips_preview_lines(self):
        csv_data = (
            b"; sigrok output\n"
            b"A0: -10.0000 V DC\n"
            b"A1:  5.5000 V DC\n"
            b"V DC,V DC\n"
            b"1.0,2.0\n"
        )
        samples = list(parse_csv(csv_data, "100kHz"))
        assert len(samples) == 1
        assert samples[0]["values"]["A0"] == pytest.approx(1.0)


class TestParseCsvSampleRates:
    """Test parse_csv with various sample rate formats."""

    def test_khz_rate(self):
        csv_data = b"logic\n1\n0\n"
        samples = list(parse_csv(csv_data, "100kHz"))
        assert samples[1]["time"] == pytest.approx(1.0 / 100e3)

    def test_mhz_rate(self):
        csv_data = b"logic\n1\n0\n"
        samples = list(parse_csv(csv_data, "1MHz"))
        assert samples[1]["time"] == pytest.approx(1e-6)

    def test_plain_suffix_rate(self):
        csv_data = b"logic\n1\n0\n"
        samples = list(parse_csv(csv_data, "1M"))
        assert samples[1]["time"] == pytest.approx(1e-6)


# ---------------------------------------------------------------------------
# Integration tests (require sigrok-cli)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_csv_format_basic(demo_client: SigrokClient):
    """Test CSV format capture with demo driver."""
    cfg = CaptureConfig(
        sample_rate="50kHz",
        samples=50,
        output_format=OutputFormat.CSV,
        channels=["vcc", "cs"],  # Select specific digital channels
    )

    result = demo_client.capture(cfg)
    assert isinstance(result, CaptureResult)
    assert isinstance(result.data, bytes)
    decoded_data = list(result.decode())
    assert isinstance(decoded_data, list)
    assert len(decoded_data) > 0
    # CSV format uses inferred names (D0, D1, etc.) based on column types
    # Channel mapping is only preserved in VCD format
    first_sample = decoded_data[0]
    assert "D0" in first_sample.values or "D1" in first_sample.values


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_csv_format_timing(demo_client: SigrokClient):
    """Test CSV format timing calculations with integer nanoseconds."""
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=50,
        output_format=OutputFormat.CSV,
        channels=["D0", "D1", "D2"],  # Select specific channels
    )

    result = demo_client.capture(cfg)
    assert isinstance(result, CaptureResult)

    # Decode the CSV data
    samples = list(result.decode())
    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify timing progresses correctly
    for sample in samples:
        assert isinstance(sample.time, float)
        # Verify timing progresses (1/100kHz = 0.00001s per sample)
        assert sample.time == pytest.approx(sample.sample * 0.00001, rel=1e-6, abs=1e-12)


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_csv_format_analog_channels(demo_client: SigrokClient):
    """Test CSV capture of analog channels with voltage values."""
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=20,
        output_format=OutputFormat.CSV,
        channels=["A0", "A1"],  # Select specific analog channels
    )

    result = demo_client.capture(cfg)
    assert isinstance(result, CaptureResult)
    assert isinstance(result.data, bytes)
    decoded_data = list(result.decode())
    assert isinstance(decoded_data, list)
    assert len(decoded_data) > 0

    # Check first sample for analog values
    first_sample = decoded_data[0]
    assert len(first_sample.values) > 0

    # Analog values should be floats (voltages)
    for _channel, value in first_sample.values.items():
        assert isinstance(value, (int, float))


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_csv_format_mixed_channels(demo_client: SigrokClient):
    """Test CSV with both digital and analog channels."""
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=30,
        output_format=OutputFormat.CSV,
        channels=["D0", "D1", "A0"],  # Mix of digital and analog
    )

    result = demo_client.capture(cfg)
    samples = list(result.decode())

    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify we have values for channels
    first_sample = samples[0]
    assert len(first_sample.values) > 0


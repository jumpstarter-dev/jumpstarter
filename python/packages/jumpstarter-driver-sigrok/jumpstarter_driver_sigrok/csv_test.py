"""Tests for CSV format parser."""

from shutil import which

import pytest

from .client import SigrokClient
from .common import CaptureConfig, CaptureResult, OutputFormat
from .driver import Sigrok
from jumpstarter.common.utils import serve


@pytest.fixture
def demo_driver_instance():
    """Create a Sigrok driver instance configured for the demo device."""
    # Demo driver has 8 digital channels (D0-D7) and 5 analog (A0-A4)
    # Map device channels to decoder-friendly semantic names
    return Sigrok(
        driver="demo",
        executable="sigrok-cli",
        channels={
            "D0": "vcc",
            "D1": "cs",
            "D2": "miso",
            "D3": "mosi",
            "D4": "clk",
            "D5": "sda",
            "D6": "scl",
            "D7": "gnd",
        },
    )


@pytest.fixture
def demo_client(demo_driver_instance):
    """Create a client for the demo Sigrok driver."""
    with serve(demo_driver_instance) as client:
        yield client


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
    decoded_data = result.decode()
    assert isinstance(decoded_data, list)
    assert len(decoded_data) > 0
    # Verify channel names are in the data
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
    samples = result.decode()
    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify timing progresses correctly
    for sample in samples:
        assert isinstance(sample.time_ns, int)
        # Verify timing progresses (1/100kHz = 10,000ns per sample)
        assert sample.time_ns == sample.sample * 10_000


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
    decoded_data = result.decode()
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
    samples = result.decode()

    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify we have values for channels
    first_sample = samples[0]
    assert len(first_sample.values) > 0


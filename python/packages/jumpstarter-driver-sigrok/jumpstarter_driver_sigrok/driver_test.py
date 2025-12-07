from shutil import which

import pytest

from .common import CaptureConfig, CaptureResult
from .driver import Sigrok
from jumpstarter.common.utils import serve


@pytest.fixture
def demo_driver_instance():
    """Create a Sigrok driver instance configured for the demo device."""
    # Demo driver has 8 digital channels (D0-D7) and 5 analog (A0-A4)
    # Map device channels to decoder-friendly semantic names
    return Sigrok(
        driver="demo",
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
    """Create a client connected to demo driver via serve()."""
    with serve(demo_driver_instance) as client:
        yield client


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_scan_demo_driver(demo_client):
    """Test scanning for demo driver via client."""
    result = demo_client.scan()
    assert "demo" in result.lower() or "Demo device" in result


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_capture_with_demo_driver(demo_client):
    """Test one-shot capture with demo driver via client.

    This test verifies client-server serialization through serve() pattern.
    """
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=100,
        output_format="srzip",
    )

    result = demo_client.capture(cfg)

    # Verify we got a proper CaptureResult Pydantic model, not just a dict
    assert isinstance(result, CaptureResult), f"Expected CaptureResult, got {type(result)}"

    # Verify model attributes work correctly - data should be bytes, not base64 string!
    assert result.data
    assert isinstance(result.data, bytes), f"Expected bytes, got {type(result.data)}"
    assert len(result.data) > 0
    assert result.output_format == "srzip"
    assert result.sample_rate == "100kHz"
    assert isinstance(result.channel_map, dict)
    assert len(result.channel_map) > 0


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_capture_csv_format(demo_client):
    """Test capture with CSV output format via client."""
    cfg = CaptureConfig(
        sample_rate="50kHz",
        samples=50,
        output_format="csv",
    )

    result = demo_client.capture(cfg)

    # Verify CaptureResult model
    assert isinstance(result, CaptureResult)
    assert isinstance(result.data, bytes)

    # Decode bytes to string for CSV parsing
    csv_text = result.data.decode("utf-8")

    # CSV should have headers and data
    assert "vcc" in csv_text or "cs" in csv_text or "clk" in csv_text


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_capture_analog_channels():
    """Test capturing analog data from oscilloscope/demo driver.

    Verifies that the API works for analog channels (oscilloscopes)
    as well as digital channels (logic analyzers).
    """
    # Create driver with analog channel mappings
    analog_driver = Sigrok(
        driver="demo",
        channels={
            "A0": "voltage_in",
            "A1": "sine_wave",
            "A2": "square_wave",
        },
    )

    with serve(analog_driver) as client:
        cfg = CaptureConfig(
            sample_rate="100kHz",
            samples=20,
            channels=["voltage_in", "sine_wave"],  # Select specific analog channels
            output_format="csv",
        )

        result = client.capture(cfg)

        # Verify we got analog data
        assert isinstance(result, CaptureResult)
        assert isinstance(result.data, bytes)

        # Parse CSV to check for analog voltage values
        csv_text = result.data.decode("utf-8")

        # Should contain voltage values with units (V, mV)
        assert "V" in csv_text or "mV" in csv_text
        # Should contain our channel names or original analog channel names
        assert "voltage_in" in csv_text or "sine_wave" in csv_text or "A0" in csv_text or "A1" in csv_text


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_capture_with_dict_config(demo_client):
    """Test capture with dict config (not CaptureConfig object).

    Verifies that dict configs are properly validated and serialized.
    """
    # Pass config as dict instead of CaptureConfig object
    cfg_dict = {
        "sample_rate": "100kHz",
        "samples": 100,
        "output_format": "srzip",
    }

    result = demo_client.capture(cfg_dict)

    # Verify we still get a proper CaptureResult model
    assert isinstance(result, CaptureResult)
    assert result.data
    assert isinstance(result.data, bytes)
    assert len(result.data) > 0
    assert result.output_format == "srzip"


@pytest.mark.skip(reason="sigrok-cli demo driver doesn't support streaming to stdout (-o -)")
def test_capture_stream_with_demo(demo_client):
    """Test streaming capture with demo driver via client.

    Note: sigrok-cli has limitations with streaming output to stdout.
    The demo driver and most output formats don't produce data when using `-o -`.
    This feature works better with real hardware and certain output formats.
    """
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=1000,
        output_format="binary",
    )

    received_bytes = 0
    chunk_count = 0

    # Collect all chunks
    for chunk in demo_client.capture_stream(cfg):
        received_bytes += len(chunk)
        chunk_count += 1

    # Should have received some data
    assert received_bytes > 0
    assert chunk_count > 0


def test_get_driver_info(demo_client):
    """Test getting driver information via client.

    Verifies dict serialization through client-server boundary.
    """
    info = demo_client.get_driver_info()

    # Verify it's a dict (not a custom object)
    assert isinstance(info, dict)
    assert info["driver"] == "demo"
    assert "channels" in info
    assert isinstance(info["channels"], dict)


def test_get_channel_map(demo_client):
    """Test getting channel mappings via client.

    Verifies dict serialization through client-server boundary.
    """
    channels = demo_client.get_channel_map()

    # Verify it's a dict with proper string keys/values
    assert isinstance(channels, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in channels.items())
    assert channels["D0"] == "vcc"
    assert channels["D4"] == "clk"
    assert channels["D7"] == "gnd"


def test_list_output_formats(demo_client):
    """Test listing supported output formats via client.

    Verifies list serialization through client-server boundary.
    """
    formats = demo_client.list_output_formats()

    # Verify it's a proper list of strings
    assert isinstance(formats, list)
    assert all(isinstance(f, str) for f in formats)
    assert "csv" in formats
    assert "srzip" in formats
    assert "vcd" in formats
    assert "binary" in formats

import subprocess
from shutil import which
from unittest.mock import patch

import pytest

from .common import CaptureConfig, CaptureResult, DecoderConfig, OutputFormat
from .driver import Sigrok
from jumpstarter.common.utils import serve


def test_ensure_executable_raises_when_none():
    """Test that _ensure_executable raises FileNotFoundError when executable is None."""
    driver = Sigrok(driver="demo", executable=None)
    with pytest.raises(FileNotFoundError, match="sigrok-cli executable not found"):
        driver.scan()


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
        output_format=OutputFormat.SRZIP,
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
def test_capture_default_format(demo_client):
    """Test capture with default output format (VCD).

    VCD is the default because it's the most efficient format:
    - Only records changes (not every sample)
    - Includes precise timing information
    - Widely supported by signal analysis tools
    """
    # Don't specify output_format - should default to VCD
    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=50,
        channels=["D0", "D1", "D2"],
    )

    result = demo_client.capture(cfg)

    # Verify we got VCD format by default
    assert isinstance(result, CaptureResult)
    assert result.output_format == OutputFormat.VCD
    assert isinstance(result.data, bytes)
    assert len(result.data) > 0

    # Verify VCD data can be decoded
    samples = list(result.decode())
    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify samples have timing information (VCD feature)
    for sample in samples:
        assert hasattr(sample, "time")
        assert isinstance(sample.time, float)
        assert hasattr(sample, "values")
        assert isinstance(sample.values, dict)


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_capture_csv_format(demo_client):
    """Test capture with CSV output format via client."""
    cfg = CaptureConfig(
        sample_rate="50kHz",
        samples=50,
        output_format=OutputFormat.CSV,
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
            output_format=OutputFormat.CSV,
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
        output_format=OutputFormat.BINARY,
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


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_csv_format(demo_client):
    """Test decoding CSV format to Sample objects with timing.

    Verifies:
    - CSV parsing works through client-server boundary
    - Sample objects have timing information
    - Values are properly typed (int/float)
    """
    from .common import OutputFormat, Sample

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

    # Verify all samples are Sample objects
    for sample in samples:
        assert isinstance(sample, Sample)
        assert isinstance(sample.sample, int)
        assert isinstance(sample.time, float)
        assert isinstance(sample.values, dict)

        # Verify timing progresses (1/100kHz = 0.00001s per sample)
        assert sample.time == pytest.approx(sample.sample * 0.00001, rel=1e-6, abs=1e-12)

        # Verify values are present
        assert len(sample.values) > 0


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_ascii_format(demo_client):
    """Test decoding ASCII format returns string visualization.

    Verifies:
    - ASCII format decoding works
    - Returns string (not bytes)
    """
    from .common import OutputFormat

    cfg = CaptureConfig(
        sample_rate="50kHz",
        samples=20,
        output_format=OutputFormat.ASCII,
        channels=["D0", "D1"],
    )

    result = demo_client.capture(cfg)
    decoded = result.decode()

    # ASCII format should return string
    assert isinstance(decoded, str)
    assert len(decoded) > 0


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_bits_format(demo_client):
    """Test decoding bits format to channel->bit sequences.

    Verifies:
    - Bits format decoding works
    - Returns dict with bit sequences
    - Channel names are mapped from device names (D0) to user-friendly names (vcc)
    """
    from .common import OutputFormat

    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=30,
        output_format=OutputFormat.BITS,
        channels=["D0", "D1", "D2"],
    )

    result = demo_client.capture(cfg)
    decoded = result.decode()

    # Bits format should return dict
    assert isinstance(decoded, dict)
    assert len(decoded) > 0

    # Should have user-friendly channel names (vcc, cs, miso) from channel_map
    # Not generic names like CH0, CH1
    assert "vcc" in decoded or "D0" in decoded
    assert "cs" in decoded or "D1" in decoded
    assert "miso" in decoded or "D2" in decoded

    # Each channel should have a list of bits
    for channel, bits in decoded.items():
        assert isinstance(channel, str)
        assert isinstance(bits, list)
        assert all(b in [0, 1] for b in bits)
        # Should have bits (at least some, exact count may vary with demo driver timing)
        assert len(bits) > 0


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_vcd_format(demo_client):
    """Test decoding VCD format to Sample objects with timing (changes only).

    Verifies:
    - VCD parsing works through client-server boundary
    - Sample objects have timing information in nanoseconds
    - Only changes are recorded (efficient representation)
    """
    from .common import OutputFormat, Sample

    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=50,
        output_format=OutputFormat.VCD,
        channels=["D0", "D1", "D2"],  # Select specific channels
    )

    result = demo_client.capture(cfg)
    assert isinstance(result, CaptureResult)

    # Decode the VCD data
    samples = list(result.decode())
    assert isinstance(samples, list)
    assert len(samples) > 0

    # Verify all samples are Sample objects
    for sample in samples:
        assert isinstance(sample, Sample)
        assert isinstance(sample.sample, int)
        assert isinstance(sample.time, float)
        assert isinstance(sample.values, dict)

        # VCD only records changes, so each sample should have at least one value
        assert len(sample.values) > 0

        # Values should be integers for digital channels
        for value in sample.values.values():
            assert isinstance(value, int)


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_vcd_analog_channels(demo_client):
    """Test decoding VCD with analog channels.

    Verifies:
    - Analog values are parsed correctly in VCD format
    - Timing information is in nanoseconds
    """
    from .common import OutputFormat, Sample

    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=30,
        output_format=OutputFormat.VCD,
        channels=["A0", "A1"],  # Analog channels
    )

    result = demo_client.capture(cfg)
    samples = list(result.decode())

    assert isinstance(samples, list)
    assert len(samples) > 0

    # Check that samples have analog values
    first_sample = samples[0]
    assert isinstance(first_sample, Sample)
    assert isinstance(first_sample.time, float)
    assert len(first_sample.values) > 0


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_unsupported_format_raises(demo_client):
    """Test that decoding unsupported formats raises NotImplementedError."""
    from .common import OutputFormat

    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=10,
        output_format=OutputFormat.BINARY,
    )

    result = demo_client.capture(cfg)

    # Binary format should not be decodable
    with pytest.raises(NotImplementedError):
        result.decode()


@pytest.mark.skipif(which("sigrok-cli") is None, reason="sigrok-cli not installed")
def test_decode_analog_csv(demo_client):
    """Test decoding CSV with analog channels (voltage values).

    Verifies:
    - Analog values are parsed as floats
    - Timing information is included
    """
    from .common import OutputFormat, Sample

    cfg = CaptureConfig(
        sample_rate="100kHz",
        samples=30,
        output_format=OutputFormat.CSV,
        channels=["A0", "A1"],  # Analog channels
    )

    result = demo_client.capture(cfg)
    samples = list(result.decode())

    assert isinstance(samples, list)
    assert len(samples) > 0

    # Check first sample for analog values
    first_sample = samples[0]
    assert isinstance(first_sample, Sample)
    assert len(first_sample.values) > 0

    # Analog values should be floats (voltages)
    for value in first_sample.values.values():
        assert isinstance(value, (int, float))


# --- Unit tests for command-building helpers (no sigrok-cli needed) ---


class TestBaseDriverArgs:
    """Tests for Sigrok._base_driver_args()."""

    def test_basic_driver_args(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli", conn="auto")
        args = driver._base_driver_args()
        assert args == ["/usr/bin/sigrok-cli", "-d", "demo"]

    def test_driver_args_with_conn(self):
        driver = Sigrok(driver="fx2lafw", executable="/usr/bin/sigrok-cli", conn="1a86.7523")
        args = driver._base_driver_args()
        assert args == ["/usr/bin/sigrok-cli", "-d", "fx2lafw:conn=1a86.7523"]

    def test_driver_args_conn_none(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli", conn=None)
        args = driver._base_driver_args()
        assert args == ["/usr/bin/sigrok-cli", "-d", "demo"]


class TestChannelArgs:
    """Tests for Sigrok._channel_args()."""

    def test_no_channels_configured(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        assert driver._channel_args(None) == []

    def test_channels_configured_no_selection(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk", "D1": "data"},
        )
        args = driver._channel_args(None)
        assert args == ["-C", "D0=clk,D1=data"]

    def test_channels_selected_by_user_name(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk", "D1": "data", "D2": "cs"},
        )
        args = driver._channel_args(["clk", "data"])
        assert "-C" in args
        channel_str = args[args.index("-C") + 1]
        assert "D0=clk" in channel_str
        assert "D1=data" in channel_str

    def test_channels_selected_by_device_name(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        args = driver._channel_args(["D0", "D1"])
        assert args == ["-C", "D0,D1"]


class TestConfigArgs:
    """Tests for Sigrok._config_args()."""

    def test_default_config(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", samples=100)
        args = driver._config_args(cfg)
        assert "-c" in args
        assert "samplerate=1M" in args[args.index("-c") + 1]
        assert "--samples" in args
        assert "100" in args

    def test_continuous_mode(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", samples=None)
        args = driver._config_args(cfg, continuous=True)
        assert "--continuous" in args
        assert "--samples" not in args

    def test_default_samples_when_none(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", samples=None)
        args = driver._config_args(cfg)
        assert "--samples" in args
        assert "1000" in args

    def test_pretrigger_config(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", samples=100, pretrigger=50)
        args = driver._config_args(cfg)
        config_str = args[args.index("-c") + 1]
        assert "pretrigger=50" in config_str


class TestTriggerArgs:
    """Tests for Sigrok._trigger_args()."""

    def test_no_triggers(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", triggers=None)
        assert driver._trigger_args(cfg) == []

    def test_with_triggers(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk", "D1": "data"},
        )
        cfg = CaptureConfig(sample_rate="1M", triggers={"clk": "rising"})
        args = driver._trigger_args(cfg)
        assert "--triggers" in args
        assert "D0=rising" in args[args.index("--triggers") + 1]


class TestDecoderArgs:
    """Tests for Sigrok._decoder_args()."""

    def test_no_decoders(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", decoders=None)
        assert driver._decoder_args(cfg) == []

    def test_simple_decoder(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "rx", "D1": "tx"},
        )
        cfg = CaptureConfig(
            sample_rate="1M",
            decoders=[DecoderConfig(name="uart", channels={"rx": "rx", "tx": "tx"})],
        )
        args = driver._decoder_args(cfg)
        assert "-P" in args
        p_arg = args[args.index("-P") + 1]
        assert p_arg.startswith("uart:")

    def test_decoder_with_options(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(
            sample_rate="1M",
            decoders=[DecoderConfig(name="uart", options={"baudrate": 115200})],
        )
        args = driver._decoder_args(cfg)
        p_arg = args[args.index("-P") + 1]
        assert "baudrate=115200" in p_arg

    def test_decoder_with_annotations(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(
            sample_rate="1M",
            decoders=[DecoderConfig(name="uart", annotations=["tx-data", "rx-data"])],
        )
        args = driver._decoder_args(cfg)
        assert "-A" in args
        a_arg = args[args.index("-A") + 1]
        assert a_arg == "uart=tx-data,rx-data"


class TestFlattenDecoders:
    """Tests for Sigrok._flatten_decoders()."""

    def test_flat_list(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        decoders = [DecoderConfig(name="uart"), DecoderConfig(name="spi")]
        result = driver._flatten_decoders(decoders)
        assert len(result) == 2
        assert result[0].name == "uart"
        assert result[1].name == "spi"

    def test_nested_stack(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        decoders = [
            DecoderConfig(
                name="spi",
                stack=[DecoderConfig(name="sdcard_spi")],
            ),
        ]
        result = driver._flatten_decoders(decoders)
        assert len(result) == 2
        assert result[0].name == "spi"
        assert result[1].name == "sdcard_spi"


class TestResolveChannel:
    """Tests for Sigrok._resolve_channel()."""

    def test_resolve_user_name(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk", "D1": "data"},
        )
        assert driver._resolve_channel("clk") == "D0"

    def test_resolve_device_name(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk"},
        )
        assert driver._resolve_channel("D0") == "D0"

    def test_resolve_unknown_device_style(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk"},
        )
        # Device-style names like "A1" should pass through even if not in channel map
        assert driver._resolve_channel("A1") == "A1"

    def test_resolve_no_channels(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        assert driver._resolve_channel("D0") == "D0"

    def test_resolve_unknown_raises(self):
        driver = Sigrok(
            driver="demo",
            executable="/usr/bin/sigrok-cli",
            channels={"D0": "clk"},
        )
        with pytest.raises(ValueError, match="not found in channel map"):
            driver._resolve_channel("unknown_channel")


class TestBuildCaptureCommand:
    """Tests for Sigrok._build_capture_command()."""

    def test_basic_capture_command(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", samples=100, output_format=OutputFormat.VCD)
        cmd, outfile = driver._build_capture_command(cfg, "/tmp/test")
        assert cmd[0] == "/usr/bin/sigrok-cli"
        assert "-d" in cmd
        assert "-O" in cmd
        assert "vcd" in cmd
        assert "-o" in cmd
        assert str(outfile).endswith("capture.vcd")


class TestBuildStreamCommand:
    """Tests for Sigrok._build_stream_command()."""

    def test_stream_command_outputs_to_stdout(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli")
        cfg = CaptureConfig(sample_rate="1M", output_format=OutputFormat.BINARY)
        cmd = driver._build_stream_command(cfg)
        assert "-o" in cmd
        assert cmd[cmd.index("-o") + 1] == "-"
        assert "--continuous" in cmd


class TestTimeoutEnforcement:
    """Tests for subprocess timeout enforcement."""

    def test_scan_timeout_propagated(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli", timeout=10)
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sigrok-cli", timeout=10)),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            driver.scan()

    def test_capture_timeout_propagated(self):
        driver = Sigrok(driver="demo", executable="/usr/bin/sigrok-cli", timeout=10)
        cfg = CaptureConfig(sample_rate="1M", samples=100)
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sigrok-cli", timeout=10)),
            pytest.raises(subprocess.TimeoutExpired),
        ):
            driver.capture(cfg)

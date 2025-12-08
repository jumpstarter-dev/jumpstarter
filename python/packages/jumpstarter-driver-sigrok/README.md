# Sigrok Driver

`jumpstarter-driver-sigrok` wraps [sigrok-cli](https://sigrok.org/wiki/Sigrok-cli) to provide logic analyzer and oscilloscope capture from Jumpstarter exporters. It supports:
- **Logic analyzers** (digital channels)
- **Oscilloscopes** (analog channels) - voltage waveform capture
- One-shot and streaming capture
- Multiple output formats with parsing (VCD, CSV, Bits, ASCII)

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-sigrok
```

## Configuration (exporter)

```yaml
export:
  sigrok:
    type: jumpstarter_driver_sigrok.driver.Sigrok
    driver: fx2lafw                     # sigrok driver (demo, fx2lafw, rigol-ds, etc.)
    conn: null                          # optional: USB VID.PID, serial path, or null for auto
    channels:                           # optional: map device channels to friendly names
      D0: clk
      D1: mosi
      D2: miso
      D3: cs
```

### Configuration Parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| `driver` | Sigrok driver name (e.g., `demo`, `fx2lafw`, `rigol-ds`) | str | yes | - |
| `conn` | Connection string (USB VID.PID, serial path, or `null` for auto-detect) | str \| None | no | None |
| `executable` | Path to `sigrok-cli` executable | str | no | Auto-detected from PATH |
| `channels` | Channel mapping from device names (D0, A0) to semantic names (clk, voltage) | dict[str, str] | no | {} (empty) |

## CaptureConfig Parameters (client-side)

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| `sample_rate` | Sampling rate (e.g., `"1M"`, `"8MHz"`, `"24000000"`) | str | no | "1M" |
| `samples` | Number of samples to capture (`None` for continuous) | int \| None | no | None |
| `pretrigger` | Number of samples to capture before trigger | int \| None | no | None |
| `triggers` | Trigger conditions by channel name (e.g., `{"cs": "falling"}`) | dict[str, str] \| None | no | None |
| `channels` | List of channel names to capture (overrides defaults) | list[str] \| None | no | None |
| `output_format` | Output format (vcd, csv, bits, ascii, srzip, binary) | str | no | "vcd" |

## Client API

- `scan()` — list devices for the configured driver
- `capture(config)` — one-shot capture, returns `CaptureResult` with base64 data
- `capture_stream(config)` — streaming capture via `--continuous`
- `get_driver_info()` — driver, conn, channel map
- `get_channel_map()` — device-to-semantic name mappings
- `list_output_formats()` — supported formats (csv, srzip, vcd, binary, bits, ascii)

## Output Formats

The driver supports multiple output formats. **VCD (Value Change Dump) is the default** because:
- ✅ **Efficient**: Only records signal changes (not every sample)
- ✅ **Precise timing**: Includes exact timestamps in nanoseconds
- ✅ **Widely supported**: Standard format for signal analysis tools
- ✅ **Mixed signals**: Handles both digital and analog data

### Available Formats

| Format | Use Case | Decoded By |
|--------|----------|------------|
| `vcd` (default) | Change-based signals with timing | `result.decode()` → `list[Sample]` |
| `csv` | All samples with timing | `result.decode()` → `list[Sample]` |
| `bits` | Bit sequences by channel | `result.decode()` → `dict[str, list[int]]` |
| `ascii` | ASCII art visualization | `result.decode()` → `str` |
| `srzip` | Raw sigrok session (for PulseView) | `result.data` (raw bytes) |
| `binary` | Raw binary data | `result.data` (raw bytes) |

### Output Format Constants

```python
from jumpstarter_driver_sigrok.common import OutputFormat

config = CaptureConfig(
    sample_rate="1MHz",
    samples=1000,
    output_format=OutputFormat.VCD,  # or CSV, BITS, ASCII, SRZIP, BINARY
)
```

## Examples

### Example 1: Simple Capture (VCD format - default)

**Python client code:**
```python
from jumpstarter_driver_sigrok.common import CaptureConfig

# Capture with default VCD format (efficient, change-based with timing)
config = CaptureConfig(
    sample_rate="1MHz",
    samples=1000,
    channels=["D0", "D1", "D2"],  # Use device channel names or mapped names
)
result = client.capture(config)

# Decode VCD to get samples with timing
samples = result.decode()  # list[Sample]
for sample in samples[:5]:
    print(f"Time: {sample.time_ns}ns, Values: {sample.values}")
```

**Equivalent sigrok-cli command:**
```bash
sigrok-cli -d fx2lafw -C D0,D1,D2 \
  -c samplerate=1MHz --samples 1000 \
  -O vcd -o /tmp/capture.vcd
```

---

### Example 2: Triggered Capture with Pretrigger

**Python client code:**
```python
from jumpstarter_driver_sigrok.common import CaptureConfig

# Capture with trigger and pretrigger buffer (VCD format - default)
config = CaptureConfig(
    sample_rate="8MHz",
    samples=20000,
    pretrigger=5000,  # Capture 5000 samples before trigger
    triggers={"D0": "rising"},  # Trigger on D0 rising edge
    channels=["D0", "D1", "D2", "D3"],
    # output_format defaults to VCD (efficient change-based format)
)
result = client.capture(config)

# Decode to analyze signal changes with precise timing
samples = result.decode()  # list[Sample] - only changes recorded
print(f"Captured {len(samples)} signal changes")

# Access timing and values
for sample in samples[:3]:
    print(f"Time: {sample.time_ns}ns, Changed: {sample.values}")
```

**Equivalent sigrok-cli command:**
```bash
sigrok-cli -d fx2lafw -C D0,D1,D2,D3 \
  -c samplerate=8MHz,samples=20000,pretrigger=5000 \
  --triggers D0=rising \
  -O vcd -o /tmp/capture.vcd
```

---

### Example 3: Oscilloscope (Analog Channels)

**Exporter configuration:**
```yaml
export:
  oscilloscope:
    type: jumpstarter_driver_sigrok.driver.Sigrok
    driver: rigol-ds  # or demo for testing
    conn: usb  # or serial path
    channels:
      A0: CH1
      A1: CH2
```

**Python client code:**
```python
from jumpstarter_driver_sigrok.common import CaptureConfig, OutputFormat

# Capture analog waveforms
config = CaptureConfig(
    sample_rate="1MHz",
    samples=10000,
    channels=["CH1", "CH2"],  # Analog channels
    output_format=OutputFormat.CSV,  # CSV for voltage values
)
result = client.capture(config)

# Parse voltage data
samples = result.decode()  # list[Sample]
for sample in samples[:5]:
    print(f"Time: {sample.time_ns}ns")
    print(f"  CH1: {sample.values.get('A0', 'N/A')}V")
    print(f"  CH2: {sample.values.get('A1', 'N/A')}V")
```

**Equivalent sigrok-cli command:**
```bash
sigrok-cli -d rigol-ds:conn=usb -C A0=CH1,A1=CH2 \
  -c samplerate=1MHz --samples 10000 \
  -O csv -o /tmp/capture.csv
```

---

### Example 4: Bits Format (Simple Bit Sequences)

**Python client code:**
```python
from jumpstarter_driver_sigrok.common import CaptureConfig, OutputFormat

# Capture in bits format (useful for visual inspection)
config = CaptureConfig(
    sample_rate="100kHz",
    samples=100,
    channels=["D0", "D1", "D2"],
    output_format=OutputFormat.BITS,
)
result = client.capture(config)

# Get bit sequences per channel
bits_by_channel = result.decode()  # dict[str, list[int]]
for channel, bits in bits_by_channel.items():
    print(f"{channel}: {''.join(map(str, bits[:20]))}")  # First 20 bits
```

**Equivalent sigrok-cli command:**
```bash
sigrok-cli -d demo -C D0,D1,D2 \
  -c samplerate=100kHz --samples 100 \
  -O bits -o /tmp/capture.bits
```

# Sigrok Driver

`jumpstarter-driver-sigrok` wraps `sigrok-cli` to provide logic analyzer and oscilloscope capture from Jumpstarter exporters. It supports:
- **Logic analyzers** (digital channels) - with protocol decoding (SPI, I2C, UART, etc.)
- **Oscilloscopes** (analog channels) - voltage waveform capture
- One-shot and streaming capture
- Decoder-friendly channel mappings
- Real-time protocol decoding

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-sigrok
```

## Configuration (exporter)

```yaml
export:
  sigrok:
    type: jumpstarter_driver_sigrok.driver.Sigrok
    driver: demo                        # sigrok driver (demo, fx2lafw, etc.)
    conn: null                          # optional: USB VID.PID or serial path
    executable: null                    # optional: path to sigrok-cli (auto-detected)
    channels:                           # channel mappings (device_name: semantic_name)
      D0: vcc
      D1: cs
      D2: miso
      D3: mosi
      D4: clk
      D5: sda
      D6: scl
```

## CaptureConfig (client-side)

```python
from jumpstarter_driver_sigrok.common import CaptureConfig, DecoderConfig

config = CaptureConfig(
    sample_rate="8MHz",
    samples=20000,
    pretrigger=5000,
    triggers={"cs": "falling"},
    decoders=[
        DecoderConfig(
            name="spi",
            channels={"clk": "clk", "mosi": "mosi", "miso": "miso", "cs": "cs"},
            annotations=["mosi-data"],
        )
    ],
)
```

This maps to:
```bash
sigrok-cli -d fx2lafw -c samplerate=8MHz,samples=20000,pretrigger=5000 --triggers D1=falling \
  -P spi:clk=D4:mosi=D3:miso=D2:cs=D1 -A spi=mosi-data
```

## Client API

- `scan()` — list devices for the configured driver
- `capture(config)` — one-shot capture, returns `CaptureResult` with base64 data
- `capture_stream(config)` — streaming capture via `--continuous`
- `get_driver_info()` — driver, conn, channel map
- `get_channel_map()` — device-to-semantic name mappings
- `list_output_formats()` — supported formats (csv, srzip, vcd, binary, bits, ascii)

## Examples

### Logic Analyzer (Digital Channels)

One-shot with trigger:
```bash
sigrok-cli -d fx2lafw -c samplerate=8MHz,samples=20000,pretrigger=5000 --triggers D0=rising -o out.sr
```

Real-time decode (SPI):
```bash
sigrok-cli -d fx2lafw -c samplerate=1M --continuous \
  -P spi:clk=D4:mosi=D3:miso=D2:cs=D1 -A spi=mosi-data
```

### Oscilloscope (Analog Channels)

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

```python
from jumpstarter_driver_sigrok.common import CaptureConfig

# Capture analog waveforms
config = CaptureConfig(
    sample_rate="1MHz",
    samples=10000,
    channels=["CH1", "CH2"],  # Analog channels
    output_format="csv",  # or "vcd" for waveform viewers
)
result = client.capture(config)
waveform_data = result.data  # bytes with voltage measurements
```

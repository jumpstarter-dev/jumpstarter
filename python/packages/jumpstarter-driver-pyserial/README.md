# PySerial Driver

`jumpstarter-driver-pyserial` provides functionality for serial port
communication.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-pyserial
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/config.yaml
:language: yaml
```

Example configuration to send commands to a MCU with DTR/RTS controlling boot process over serial port, with --no-output (fire-and-forget mode):
```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/config_configuration.yaml
:language: yaml
```


### Config parameters

| Parameter      | Description                                                                                                                                          | Type  | Required | Default |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------- | ------- |
| url            | The serial port to connect to, in [pyserial format](https://pyserial.readthedocs.io/en/latest/url_handlers.html)                                     | str   | yes      |         |
| baudrate       | The baudrate to use for the serial connection                                                                                                        | int   | no       | 115200  |
| check_present | Check if the serial port exists during exporter initialization, disable if you are connecting to a dynamically created port (i.e. USB from your DUT) | bool  | no       | True    |
| cps            | Characters per second throttling limit. When set, data transmission will be throttled to simulate slow typing. Useful for devices that can't handle fast input | float | no       | None    |
| disable_hupcl  | Disable HUPCL on POSIX systems to avoid toggling DTR/RTS on close (can prevent MCU reset on serial disconnect)                                       | bool  | no       | False   |

### NVDemuxSerial Driver

The `NVDemuxSerial` driver provides serial access to NVIDIA Tegra demultiplexed UART channels using the [nv_tcu_demuxer](https://docs.nvidia.com/jetson/archives/r38.2.1/DeveloperGuide/AT/JetsonLinuxDevelopmentTools/TegraCombinedUART.html) tool. It automatically handles device reconnection when the target device restarts.

The nv_tcu_demuxer tool can be obtained from the NVIDIA Jetson BSP, at this path: `Linux_for_Tegra/tools/demuxer/nv_tcu_demuxer`.

#### Multi-Instance Support

Multiple driver instances can share a single demuxer process by specifying different target channels. This allows simultaneous access to multiple UART channels (CCPLEX, BPMP, SCE, etc.) from the same physical device.

#### Configuration

##### Single channel example:

```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/config_single_channel_example.yaml
:language: yaml
```

##### Multiple channels example:

```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/config_multiple_channels_example.yaml
:language: yaml
```

#### Config parameters

| Parameter      | Description                                                                                     | Type  | Required | Default                                                                   |
| -------------- | ----------------------------------------------------------------------------------------------- | ----- | -------- | ------------------------------------------------------------------------- |
| demuxer_path   | Path to the `nv_tcu_demuxer` binary                                                             | str   | yes      |                                                                           |
| device         | Device path or glob pattern for auto-detection                                                  | str   | no       | `/dev/serial/by-id/usb-NVIDIA_Tegra_On-Platform_Operator_*-if01`          |
| target         | Target channel to extract from demuxer output                                                   | str   | no       | `CCPLEX: 0`                                                               |
| chip           | Chip type for demuxer (`T234` for Orin, `T264` for Thor)                                        | str   | no       | `T264`                                                                    |
| baudrate       | Baud rate for the serial connection                                                             | int   | no       | 115200                                                                    |
| cps            | Characters per second throttling limit                                                          | float | no       | None                                                                      |
| timeout        | Timeout in seconds waiting for demuxer to detect pts                                            | float | no       | 10.0                                                                      |
| poll_interval  | Interval in seconds to poll for device reappearance after disconnect                            | float | no       | 1.0                                                                       |

#### Device Auto-Detection

The `device` parameter supports glob patterns for automatic device discovery:

```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/config_device_auto_detection.yaml
:language: yaml
```

#### Auto-Recovery

When the target device restarts (e.g., power cycle), the serial device disappears and the demuxer exits. The driver automatically:

1. Detects the device disconnection
2. Polls for the device to reappear
3. Restarts the demuxer with the new device
4. Discovers the new pts path (which changes on each restart)

Active connections will receive errors when the device disconnects. Clients should reconnect, and the driver will wait for the device to be available again.

#### Configuration Validation / Limitations

When using multiple driver instances, all instances must have compatible configurations:

- **demuxer_path**: Must be identical across all instances
- **device**: Must be identical across all instances
- **chip**: Must be identical across all instances
- **target**: Must be unique for each instance (no duplicates allowed)

If these requirements are not met, the driver will raise a `ValueError` during initialization.


## Usage

The pyserial driver provides two CLI commands for interacting with serial ports:

### start_console

Start an interactive serial console with direct terminal access.

```bash
j serial start-console
```

Exit the console by pressing CTRL+B three times.

### pipe

Pipe serial port data to stdout or a file. Automatically detects if stdin is piped and enables bidirectional mode.

When stdin is used, commands are sent until EOF, then continues monitoring serial output until Ctrl+C.

Use `--no-output` for fire-and-forget mode: send stdin to serial and exit at EOF without reading serial output.

```bash
# Log serial output to stdout
j serial pipe

# Log serial output to a file
j serial pipe -o serial.log

# Send command to serial, then continue monitoring output
echo "hello" | j serial pipe

# Send commands from file, then continue monitoring output
cat commands.txt | j serial pipe -o serial.log

# Force bidirectional mode (interactive)
j serial pipe -i

# Append to log file instead of overwriting
j serial pipe -o serial.log -a

# Disable stdin input even when piped
cat data.txt | j serial pipe --no-input

# Fire-and-forget: send stdin to serial and exit at EOF (no serial output)
cat commands.txt | j serial pipe --no-output
```

#### Options

- `-o, --output FILE`: Write serial output to a file instead of stdout
- `-i, --input`: Force enable stdin to serial port (auto-detected if piped)
- `--no-input`: Disable stdin to serial port, even if stdin is piped
- `-a, --append`: Append to output file instead of overwriting
- `--no-output`: Disable serial output handling (stdin -> serial only, exits at EOF)

Notes:
- `--no-output` cannot be combined with `--output` or `--append`.
- `--no-output` requires stdin input (piped stdin or `--input`).

Exit with Ctrl+C.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_pyserial.client.PySerialClient()
    :members: pexpect, open, stream, open_stream, close
```

### Examples

Using expect with a context manager
```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/usage.py
:language: python
```

Using expect without a context manager
```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/usage_examples.py
:language: python
```

Using a simple BlockingStream with a context manager
```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/usage_examples_1.py
:language: python
```

Using a simple BlockingStream without a context manager
```{literalinclude} ../../../../../packages/jumpstarter-driver-pyserial/examples/usage_examples_2.py
:language: python
```

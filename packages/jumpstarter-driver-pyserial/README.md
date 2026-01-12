# PySerial driver

`jumpstarter-driver-pyserial` provides functionality for serial port
communication.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-pyserial
```

## Configuration

Example configuration:

```yaml
export:
  serial:
    type: jumpstarter_driver_pyserial.driver.PySerial
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
      cps: 10  # Optional: throttle to 10 characters per second
```

### Config parameters

| Parameter      | Description                                                                                                                                          | Type  | Required | Default |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------- | ------- |
| url            | The serial port to connect to, in [pyserial format](https://pyserial.readthedocs.io/en/latest/url_handlers.html)                                     | str   | yes      |         |
| baudrate       | The baudrate to use for the serial connection                                                                                                        | int   | no       | 115200  |
| check_present | Check if the serial port exists during exporter initialization, disable if you are connecting to a dynamically created port (i.e. USB from your DUT) | bool  | no       | True    |
| cps            | Characters per second throttling limit. When set, data transmission will be throttled to simulate slow typing. Useful for devices that can't handle fast input | float | no       | None    |

## CLI Commands

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
```

#### Options

- `-o, --output FILE`: Write serial output to a file instead of stdout
- `-i, --input`: Force enable stdin to serial port (auto-detected if piped)
- `--no-input`: Disable stdin to serial port, even if stdin is piped
- `-a, --append`: Append to output file instead of overwriting

Exit with Ctrl+C.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_pyserial.client.PySerialClient()
    :members: pexpect, open, stream, open_stream, close
```

### Examples

Using expect with a context manager
```{testcode}
with pyserialclient.pexpect() as session:
    session.sendline("Hello, world!")
    session.expect("Hello, world!")
```

Using expect without a context manager
```{testcode}
session = pyserialclient.open()
session.sendline("Hello, world!")
session.expect("Hello, world!")
pyserialclient.close()
```

Using a simple BlockingStream with a context manager
```{testcode}
with pyserialclient.stream() as stream:
    stream.send(b"Hello, world!")
    data = stream.receive()
```

Using a simple BlockingStream without a context manager
```{testcode}
stream = pyserialclient.open_stream()
stream.send(b"Hello, world!")
data = stream.receive()
```

```{testsetup} *
from jumpstarter_driver_pyserial.driver import PySerial
from jumpstarter.common.utils import serve

instance = serve(PySerial(url="loop://"))

pyserialclient = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```

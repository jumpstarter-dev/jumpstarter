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
```

### Config parameters

| Parameter      | Description                                                                                                                                          | Type | Required | Default |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | -------- | ------- |
| url            | The serial port to connect to, in [pyserial format](https://pyserial.readthedocs.io/en/latest/url_handlers.html)                                     | str  | yes      |         |
| baudrate       | The baudrate to use for the serial connection                                                                                                        | int  | no       | 115200  |
| check_existing | Check if the serial port exists during exporter initialization, disable if you are connecting to a dynamically created port (i.e. USB from your DUT) | bool | no       | True    |

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

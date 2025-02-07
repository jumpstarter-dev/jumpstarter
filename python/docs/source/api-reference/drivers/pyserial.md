# PySerial

**driver**: `jumpstarter_driver_pyserial.driver.PySerial`

The methods of this client are dynamic, and they are generated from
the `methods` field of the exporter driver configuration.

## Driver configuration
```yaml
export:
  my_serial:
    type: ""jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB0"
      baudrate: 115200
```
### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| url | The serial port to connect to, in [pyserial format](https://pyserial.readthedocs.io/en/latest/url_handlers.html)  | str | yes | |
| baudrate | The baudrate to use for the serial connection | int | no | 115200 |


## PySerialClient API
```{eval-rst}
.. autoclass:: "jumpstarter_driver_pyserial.client.PySerialClient
    :members: pexpect, open, stream, open_stream, close
```

## Examples
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
session.close()
```

Using a simple BlockingStream with a context manager
```{testcode}
with pyserialclient.stream() as stream:
    stream.write(b"Hello, world!")
    data = stream.read(13)
```

Using a simple BlockingStream without a context manager
```{testcode}
stream = pyserialclient.open_stream()
stream.write(b"Hello, world!")
data = stream.read(13)
stream.close()
```

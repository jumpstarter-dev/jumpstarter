# Proxy driver

`jumpstarter-driver-proxy` provides functionality for creating "proxies" or aliases of other drivers to present a desired view of the tree of devices to the client. It's also useful when several drivers need configured access to a common resource, like a serial port or a network connection.

## Installation

```bash
pip install jumpstarter-driver-proxy
```

## Configuration

Example configuration:

```{literalinclude} proxy.yaml
:language: yaml
```

## API Reference

```{doctest}
>>> root.foo.bar.power.on() # instead of
>>> root.proxy.on()         # you can do
```

```{testsetup} *
from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
from jumpstarter.common.utils import serve

instance = serve(
    ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/proxy.yaml"
).instantiate())

root = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```

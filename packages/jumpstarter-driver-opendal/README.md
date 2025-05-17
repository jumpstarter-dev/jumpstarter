# OpenDAL driver

`jumpstarter-driver-opendal` provides functionality for interacting with
storages attached to the exporter.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-opendal
```

## Configuration

Example configuration:

```{literalinclude} opendal.yaml
:language: yaml
```

## API Reference

### Examples

```{doctest}
>>> from tempfile import NamedTemporaryFile
>>> opendal.create_dir("test/directory/")
>>> opendal.write_bytes("test/directory/file", b"hello")
>>> assert opendal.hash("test/directory/file", "md5") == "5d41402abc4b2a76b9719d911017c592"
>>> opendal.remove_all("test/")
```

```{testsetup} *
from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
from jumpstarter.common.utils import serve

instance = serve(
    ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/opendal.yaml"
).instantiate())

opendal = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```

### Client API

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.OpendalClient()
    :members:

.. autoclass:: jumpstarter_driver_opendal.client.OpendalFile()
    :members:

.. autoclass:: jumpstarter_driver_opendal.common.Metadata()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.EntryMode()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.PresignedRequest()
    :members:
    :undoc-members:
    :exclude-members: model_config

.. autoclass:: jumpstarter_driver_opendal.common.Capability()
    :members:
    :undoc-members:
    :exclude-members: model_config
```

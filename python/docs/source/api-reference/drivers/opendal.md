# Opendal driver

The Opendal driver is a driver for interacting with storages attached to the exporter.

## Driver configuration

```{literalinclude} opendal.yaml
:language: yaml
```

## Examples

```{doctest}
>>> from tempfile import NamedTemporaryFile
>>> opendal.create_dir("test/directory/")
>>> remote_file = opendal.open("test/directory/file", "wb")
>>> with NamedTemporaryFile() as local_file:
...     assert local_file.write(b"hello") == 5
...     remote_file.write(local_file.name)
>>> remote_file.close()
```

```{testsetup} *
from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
from jumpstarter.common.utils import serve

instance = serve(
    ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/opendal.yaml"
).instantiate())

opendal = instance.__enter__()
```

```{testcleanup} *
instance.__exit__(None, None, None)
```


## Client API

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

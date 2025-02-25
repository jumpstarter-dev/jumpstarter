# Opendal driver

The Opendal driver is a driver for interacting with storages attached to the exporter.

## Driver configuration

```{literalinclude} opendal.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/opendal.yaml").instantiate()
Opendal(...)
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

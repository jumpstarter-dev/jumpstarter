>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/api-reference/drivers/sdwire.yaml").instantiate()
Traceback (most recent call last):
...
FileNotFoundError: failed to find sd-wire device

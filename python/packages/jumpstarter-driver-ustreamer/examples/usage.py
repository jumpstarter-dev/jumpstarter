>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/ustreamer.yaml").instantiate()
Traceback (most recent call last):
...
io.UnsupportedOperation: fileno

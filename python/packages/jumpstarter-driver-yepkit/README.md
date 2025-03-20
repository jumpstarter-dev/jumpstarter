# Jumpstarter Driver for the ykush USB Hub from Yepkit

This driver is for the ykush USB Hub from Yepkit. It allows you to control the power of each port of the hub.

If you want to test this locally, you can use the following commands from the root of the repository:

```bash
sudo $(which uv) run jmp shell --exporter-config ./packages/jumpstarter-driver-yepkit/examples/exporter.yaml
```

Please note that sudo is necessary to gain access to the raw USB interfaces.

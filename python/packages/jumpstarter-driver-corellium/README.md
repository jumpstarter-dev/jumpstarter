# Corellium Jumpstarter Driver

A Jumpstarter driver that manages virtual devices in [Corellium](https://corellium.com).

It implements the following interfaces:

* PowerInterface

## Usage

Check the [examples folder](./examples) for example files.

### Config Exporter

You can run an exporter by running: `jmp exporter shell -c $file`:

```yml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
# endpoint and token are intentionally left empty
metadata:
  namespace: default
  name: corellium-demo
endpoint: ""
token: ""
export:
  rd1ae:
    type: jumpstarter_driver_corellium.driver.Corellium
    config:
      project_id: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
      device_name: "jmp-rd1ae"
      device_flavor: "kronos"
```

```yml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
# endpoint and token are intentionally left empty
metadata:
  namespace: default
  name: corellium-demo
endpoint: ""
token: ""
export:
  rd1ae:
    type: jumpstarter_driver_corellium.driver.Corellium
    config:
      project_id: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
      device_name: "jmp-rd1ae"
      device_flavor: "kronos"
      device_os: "1.0"
      device_build: "Critical Application Monitor (Baremetal)"
```

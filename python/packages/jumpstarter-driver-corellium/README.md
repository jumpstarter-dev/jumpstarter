# Corellium Driver

`jumpstarter-driver-corellium` provides functionality for interacting with
[Corellium](https://corellium.com) virtualization platform.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-corellium
```

## Configuration

Example configuration:

```yaml
export:
  corellium:
    type: jumpstarter_driver_corellium.driver.Corellium
    config:
      project_id: "778f00af-5e9b-40e6-8e7f-c4f14b632e9c"
      device_name: "jmp-rd1ae"
      device_flavor: "kronos"
      # Optional parameters
      # device_os: "1.0"
      # device_build: "Critical Application Monitor (Baremetal)"
```

### ExporterConfig Example

You can run an exporter by running: `jmp exporter shell -c $file`:

```yaml
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

```yaml
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

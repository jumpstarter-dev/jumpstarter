# HTTP Power Driver

`jumpstarter-driver-http-power` provides functionality for controlling power via HTTP endpoints and reading power measurements.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-http-power
```

## Configuration

Example configuration:

```yaml
export:
  http_power:
    type: jumpstarter_driver_http_power.driver.HttpPower
    config:
      name: "device"
      power_on:
        url: "http://power-controller.local/api/power/on"
        method: "POST"
        data: "action=on"
      power_off:
        url: "http://power-controller.local/api/power/off"
        method: "POST"
        data: "action=off"
      power_read:
        url: "http://power-controller.local/api/power/status"
        method: "GET"
      auth:
        basic:
          user: "admin"
          password: "secret"
```

### Example configuration for Shelly Smart Plug (Gen1):

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: ""
token: ""
export:
  power:
    type: jumpstarter_driver_http_power.driver.HttpPower
    config:
      name: "my-splug"
      power_on:
        url: "http://192.168.1.65/relay/0?turn=on"
      power_off:
        url: "http://192.168.1.65/relay/0?turn=off"
      auth:
        basic:
          user: admin
          password: something
```

### Example configuration for Shelly Smart Plug (Gen2/Gen3):

Gen2/Gen3 plugs (e.g. Plug S G3) use the RPC API and report `voltage`/`current`
as top-level keys, so `read()` works with no path configuration:

```yaml
export:
  power:
    type: jumpstarter_driver_http_power.driver.HttpPower
    config:
      name: "my-splug"
      power_on:
        url: "http://192.168.0.111/rpc/Switch.Set?id=0&on=true"
      power_off:
        url: "http://192.168.0.111/rpc/Switch.Set?id=0&on=false"
      power_read:
        url: "http://192.168.0.111/rpc/Switch.GetStatus?id=0"
```

Using the `examples/exporter-shelly-gen3.yaml` config, power on, take 4 measurements one second apart, then power off:

```shell
$ jmp shell --exporter-config exporter.yaml -- sh -c 'j power on && j power read -n 4 -i 1 && j power off'
[06/23/26 23:00:26] INFO     [driver.HttpPower] Powering on shellyplugsg3 via
HTTP
voltage=236.6 V  current=0.0 A  apparent_power=0.0 VA
voltage=236.5 V  current=0.09 A  apparent_power=21.285 VA
voltage=236.6 V  current=0.016 A  apparent_power=3.7856 VA
voltage=236.6 V  current=0.0 A  apparent_power=0.0 VA
[06/23/26 23:00:30] INFO     Powering off shellyplugsg3 via HTTP
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| name | Name of the device, for logging purposes | str | no | "device" |
| power_on | HTTP endpoint config for powering on | HttpEndpointConfig | yes | |
| power_off | HTTP endpoint config for powering off | HttpEndpointConfig | yes | |
| power_read | HTTP endpoint config for reading power measurements. When unset, `read()` raises rather than returning a fake zero measurement | HttpEndpointConfig | no | None |
| auth | Authentication configuration | HttpAuthConfig | no | None |
| auth.basic | Basic authentication credentials | HttpBasicAuth | no | None |

#### HttpEndpointConfig parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| url | The HTTP endpoint URL | str | yes | |
| method | HTTP method (GET, POST, PUT, etc.) | str | no | "GET" |
| data | Request body data for POST/PUT/PATCH requests | str | no | None |
| voltage_path | On a `power_read` endpoint: dotted JSON path to the voltage value (e.g. `emeter.voltage`, `StatusSNS.ENERGY.Voltage`) | str | no | top-level `voltage` |
| current_path | On a `power_read` endpoint: dotted JSON path to the current value | str | no | top-level `current` |

#### HttpBasicAuth parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| user | Username for basic authentication | str | yes | |
| password | Password for basic authentication | str | yes | |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :members: on, off, read, cycle
    :no-index:
```

### Examples

Basic power control:
```python
# Power on the device
http_power_client.on()

# Power off the device
http_power_client.off()
```


Reading measurements: `read()` parses the JSON returned by `power_read` and pulls
voltage and current from `voltage_path` / `current_path` (defaulting to top-level
`voltage` and `current` keys). A field the device doesn't report reads as `0.0`; a
configured path that isn't found raises an error.

```yaml
      power_read:
        url: "http://192.168.1.65/cm?cmnd=Status%2010"   # Tasmota
        voltage_path: "StatusSNS.ENERGY.Voltage"
        current_path: "StatusSNS.ENERGY.Current"
```

```{note}
Authentication is optional and supports HTTP Basic Auth only.
```

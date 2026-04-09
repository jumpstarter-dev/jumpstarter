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

### Example configuration for Shelly Smart Plug:

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

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| name | Name of the device, for logging purposes | str | no | "device" |
| power_on | HTTP endpoint config for powering on | HttpEndpointConfig | yes | |
| power_off | HTTP endpoint config for powering off | HttpEndpointConfig | yes | |
| power_read | HTTP endpoint config for reading power measurements | HttpEndpointConfig | no | None |
| auth | Authentication configuration | HttpAuthConfig | no | None |
| auth.basic | Basic authentication credentials | HttpBasicAuth | no | None |

#### HttpEndpointConfig parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| url | The HTTP endpoint URL | str | yes | |
| method | HTTP method (GET, POST, PUT, etc.) | str | no | "GET" |
| data | Request body data for POST/PUT/PATCH requests | str | no | None |

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


## Notes

- The power reading response parsing is not yet implemented. The driver currently returns dummy values (0.0V, 0.0A).
- Authentication is optional and currently supports HTTP Basic Auth only.
- All HTTP requests will raise exceptions on HTTP error status codes.

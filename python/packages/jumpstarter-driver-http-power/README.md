# HTTP Power Driver

`jumpstarter-driver-http-power` provides functionality for controlling power via HTTP endpoints and reading power measurements.

## Installation

```{code-block} shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-http-power
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-http-power/examples/config.yaml
:language: yaml
```

### Example configuration for Shelly Smart Plug:

```{literalinclude} ../../../../../packages/jumpstarter-driver-http-power/examples/config_shelly.yaml
:language: yaml
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
```{literalinclude} ../../../../../packages/jumpstarter-driver-http-power/examples/usage.py
:language: python
```


```{note}
Power reading response parsing is not yet implemented - the driver returns
dummy values (0.0V, 0.0A). Authentication is optional and supports HTTP
Basic Auth only.
```

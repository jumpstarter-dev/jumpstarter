# Tasmota driver

`jumpstarter-driver-tasmota` provides functionality for interacting with tasmota compatible devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-tasmota
```

## Configuration

Example configuration:

```yaml
export:
  power:
    type: jumpstarter_driver_tasmota.driver.TasmotaPower
```

### Config parameters

| Parameter    | Description                                                     | Default  |
|--------------|-----------------------------------------------------------------|----------|
| `host`       | MQTT broker hostname or IP address                              | Required |
| `port`       | MQTT broker port                                                | 1883     |
| `tls`        | MQTT broker TLS enabled                                         | True     |
| `client_id`  | Client identifier for MQTT connection                           |          |
| `transport`  | Transport protocol, one of "tcp", "websockets", "unix"          | "tcp"    |
| `timeout`    | Timeout in seconds for operations                               |          |
| `username`   | Username for MQTT authentication                                |          |
| `password`   | Password for MQTT authentication                                |          |
| `cmnd_topic` | MQTT topic for sending commands to the Tasmota device           | Required |
| `stat_topic` | MQTT topic for receiving status updates from the Tasmota device | Required |

## API Reference

The tasmota power driver provides a `PowerClient` with the following API:

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :no-index:
    :members: on, off
```

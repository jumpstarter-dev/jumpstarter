# SOME/IP Driver

`jumpstarter-driver-someip` provides SOME/IP (Scalable service-Oriented Middleware over IP)
protocol operations for Jumpstarter. This driver wraps the
[opensomeip](https://github.com/vtz/opensomeip-python) Python binding to enable remote
RPC calls, service discovery, raw messaging, and event subscriptions with automotive
ECUs over Ethernet.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-someip
```

## Configuration

| Parameter         | Type        | Default       | Description                                           |
|-------------------|-------------|---------------|-------------------------------------------------------|
| `host`            | str         | required      | Local IP address to bind                              |
| `port`            | int         | 30490         | Local SOME/IP port                                    |
| `transport_mode`  | str         | `UDP`         | Transport protocol: `UDP` or `TCP`                    |
| `multicast_group` | str         | `239.127.0.1` | SD multicast group address                            |
| `multicast_port`  | int         | 30490         | SD multicast port                                     |
| `remote_host`     | str \| None | `None`        | Remote ECU IP for static routing (bypasses SD)        |
| `remote_port`     | int \| None | `None`        | Remote ECU port (defaults to `port` when `remote_host` is set) |

### UDP (default)

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/config.yaml
:language: yaml
```

### TCP

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/config_tcp.yaml
:language: yaml
```

### Static remote endpoint (no Service Discovery)

When the target ECU does not run SOME/IP-SD (e.g. Zephyr firmware with
multicast TX disabled), set `remote_host` and optionally `remote_port`
to send messages directly without Service Discovery:

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/config_static_remote_endpoint_no_service_discov.yaml
:language: yaml
```

## Usage

### RPC Call

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/usage.py
:language: python
```

### Service Discovery + RPC

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/usage_service_discovery_rpc.py
:language: python
```

### Event Subscription

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/usage_event_subscription.py
:language: python
```

### Raw Messaging

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/usage_raw_messaging.py
:language: python
```

### Connection Management

```{literalinclude} ../../../../../packages/jumpstarter-driver-someip/examples/usage_connection_management.py
:language: python
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_someip.driver.SomeIp()
```

# SOME/IP Driver

`jumpstarter-driver-someip` provides SOME/IP (Scalable service-Oriented MiddlewarE over IP)
protocol operations for Jumpstarter. This driver wraps the
[opensomeip](https://github.com/vtz/opensomeip-python) Python binding to enable remote
RPC calls, service discovery, raw messaging, and event subscriptions with automotive
ECUs over Ethernet.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-someip
```

## Configuration

| Parameter         | Type   | Default       | Description                                |
|-------------------|--------|---------------|--------------------------------------------|
| `host`            | str    | required      | Local IP address to bind                   |
| `port`            | int    | 30490         | Local SOME/IP port                         |
| `transport_mode`  | str    | `UDP`         | Transport protocol: `UDP` or `TCP`         |
| `multicast_group` | str    | `239.127.0.1` | SD multicast group address                 |
| `multicast_port`  | int    | 30490         | SD multicast port                          |

### Example exporter configuration

```yaml
export:
  someip:
    type: jumpstarter_driver_someip.driver.SomeIp
    config:
      host: "192.168.1.100"
      port: 30490
      transport_mode: UDP
      multicast_group: "239.127.0.1"
      multicast_port: 30490
```

## Client API

### RPC

| Method                                               | Description                 |
|------------------------------------------------------|-----------------------------|
| `rpc_call(service_id, method_id, payload, timeout)`  | Make a SOME/IP RPC call     |

### Raw Messaging

| Method                                          | Description                        |
|-------------------------------------------------|------------------------------------|
| `send_message(service_id, method_id, payload)`  | Send a raw SOME/IP message         |
| `receive_message(timeout)`                      | Receive a raw SOME/IP message      |

### Service Discovery

| Method                                                       | Description                       |
|--------------------------------------------------------------|-----------------------------------|
| `find_service(service_id, instance_id=0xFFFF, timeout=5.0)`  | Find services via SOME/IP-SD      |

### Events

| Method                                  | Description                        |
|-----------------------------------------|------------------------------------|
| `subscribe_eventgroup(eventgroup_id)`   | Subscribe to an event group        |
| `unsubscribe_eventgroup(eventgroup_id)` | Unsubscribe from an event group    |
| `receive_event(timeout)`               | Receive next event notification    |

### Connection Management

| Method              | Description                              |
|---------------------|------------------------------------------|
| `close_connection()` | Close the SOME/IP connection            |
| `reconnect()`       | Reconnect to the SOME/IP endpoint        |

## Example Usage

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # RPC call
    response = someip.rpc_call(0x1234, 0x0001, b"\x01\x02\x03")
    print(f"Response: {bytes.fromhex(response.payload)}")

    # Raw messaging
    someip.send_message(0x1234, 0x0001, b"\xAA\xBB")
    msg = someip.receive_message(timeout=2.0)

    # Service discovery (instance_id defaults to 0xFFFF = any)
    services = someip.find_service(0x1234, timeout=3.0)
    for svc in services:
        print(f"Found: service={svc.service_id:#06x} instance={svc.instance_id:#06x}")

    # Events
    someip.subscribe_eventgroup(1)
    event = someip.receive_event(timeout=5.0)
    print(f"Event: {bytes.fromhex(event.payload)}")
    someip.unsubscribe_eventgroup(1)
```

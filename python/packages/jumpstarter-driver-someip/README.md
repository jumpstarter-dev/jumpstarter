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

### UDP (default)

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

### TCP

```yaml
export:
  someip:
    type: jumpstarter_driver_someip.driver.SomeIp
    config:
      host: "192.168.1.100"
      port: 30490
      transport_mode: TCP
```

## API Reference

### RPC

- `rpc_call(service_id, method_id, payload, timeout=5.0)` — Make a SOME/IP RPC call and return the response

### Raw Messaging

- `send_message(service_id, method_id, payload)` — Send a raw SOME/IP message
- `receive_message(timeout=2.0)` — Receive a raw SOME/IP message

### Service Discovery

- `find_service(service_id, instance_id=0xFFFF, timeout=5.0)` — Find services via SOME/IP-SD; use `instance_id=0xFFFF` (default) to match any instance

### Events

- `subscribe_eventgroup(eventgroup_id)` — Subscribe to a SOME/IP event group
- `unsubscribe_eventgroup(eventgroup_id)` — Unsubscribe from a SOME/IP event group
- `receive_event(timeout=5.0)` — Receive next event notification

### Connection Management

- `close_connection()` — Close the SOME/IP connection
- `reconnect()` — Reconnect to the SOME/IP endpoint

## Example Usage

### RPC Call

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    response = someip.rpc_call(0x1234, 0x0001, b"\x01\x02\x03")
    print(f"Response: {bytes.fromhex(response.payload)}")
    print(f"Return code: {response.return_code}")
```

### Service Discovery + RPC

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Discover available services
    services = someip.find_service(0x1234, timeout=3.0)
    for svc in services:
        print(f"Found: service={svc.service_id:#06x} instance={svc.instance_id:#06x}")

    # Call the first discovered service
    if services:
        resp = someip.rpc_call(0x1234, 0x0001, b"\x10\x20")
        print(f"RPC result: {resp.payload}")
```

### Event Subscription

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Subscribe to event group 1
    someip.subscribe_eventgroup(1)

    # Wait for event notifications
    try:
        event = someip.receive_event(timeout=10.0)
        print(f"Event service={event.service_id:#06x} id={event.event_id:#06x}")
        print(f"Payload: {bytes.fromhex(event.payload)}")
    finally:
        someip.unsubscribe_eventgroup(1)
```

### Raw Messaging

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    someip.send_message(0x1234, 0x0001, b"\xAA\xBB")
    msg = someip.receive_message(timeout=2.0)
    print(f"Received from service={msg.service_id:#06x}: {msg.payload}")
```

### Connection Management

```python
from jumpstarter.common.utils import env

with env() as client:
    someip = client.someip

    # Perform operations...
    someip.rpc_call(0x1234, 0x0001, b"\x01")

    # Reconnect after network disruption
    someip.reconnect()

    # Continue operations
    someip.rpc_call(0x1234, 0x0001, b"\x02")

    # Clean up
    someip.close_connection()
```

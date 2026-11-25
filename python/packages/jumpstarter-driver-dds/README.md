# DDS Driver

`jumpstarter-driver-dds` provides DDS (Data Distribution Service) publish/subscribe
communication for Jumpstarter using [Eclipse CycloneDDS](https://cyclonedds.io/).

DDS is a middleware protocol standard (OMG DDS) for data-centric connectivity, widely used
in automotive (AUTOSAR Adaptive), robotics (ROS 2), and IoT applications. This driver
enables remote DDS domain participation, topic management, and pub/sub messaging.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-dds
```

The package does not pull in the CycloneDDS Python bindings. Install them on
the exporter as well; without them the driver fails to load unless
`use_mock: true` is set:

```shell
pip3 install cyclonedds
```

## Configuration

| Parameter              | Type   | Default      | Description                              |
|------------------------|--------|--------------|------------------------------------------|
| `domain_id`            | int    | 0            | DDS domain ID                            |
| `default_reliability`  | str    | `RELIABLE`   | Default QoS reliability (`RELIABLE` or `BEST_EFFORT`) |
| `default_durability`   | str    | `VOLATILE`   | Default QoS durability (`VOLATILE`, `TRANSIENT_LOCAL`, `TRANSIENT`, `PERSISTENT`) |
| `default_history_depth`| int    | 10           | Default history depth for topics         |
| `use_mock`             | bool   | false        | Use an in-memory backend instead of CycloneDDS (for testing) |

### Example exporter config

```yaml
export:
  dds:
    type: jumpstarter_driver_dds.driver.Dds
    config:
      domain_id: 0
      default_reliability: RELIABLE
      default_durability: VOLATILE
      default_history_depth: 10
```

## Client API

### Domain Lifecycle

| Method                   | Description                                  |
|--------------------------|----------------------------------------------|
| `connect()`              | Connect to the DDS domain, create participant |
| `disconnect()`           | Disconnect and release all resources          |
| `get_participant_info()` | Get domain participant information            |

### Topic Management

| Method                                                 | Description                        |
|--------------------------------------------------------|------------------------------------|
| `create_topic(name, fields, reliability, durability, history_depth)` | Create a topic with schema and QoS; QoS arguments left out use the configured defaults |
| `list_topics()`                                        | List all registered topics         |

With CycloneDDS, every field in a topic schema is typed as a string.

### Publish / Subscribe

| Method                              | Description                              |
|-------------------------------------|------------------------------------------|
| `publish(topic_name, data)`         | Publish a data sample to a topic         |
| `read(topic_name, max_samples=10)`  | Read (take) samples from a topic         |
| `monitor(topic_name, max_iterations=0)` | Stream samples from a topic as they arrive, polling every 0.1 s; `max_iterations=0` polls until the stream is closed |

`read` and `monitor` take from the same reader buffer, so do not use both on
the same topic at the same time.

### QoS Options

**Reliability:**

- `RELIABLE` - Guaranteed delivery with acknowledgment
- `BEST_EFFORT` - Fire-and-forget, lowest latency

**Durability:**

- `VOLATILE` - Samples only delivered to currently connected readers
- `TRANSIENT_LOCAL` - Late-joining readers receive cached samples
- `TRANSIENT` - Samples survive writer restarts
- `PERSISTENT` - Samples survive system restarts

## Example Usage

```python
from jumpstarter.common.utils import env

with env() as client:
    dds = client.dds

    # Connect to DDS domain
    dds.connect()

    # Create a topic with schema
    dds.create_topic("sensor/temperature", ["value", "unit", "location"])

    # Publish data
    dds.publish("sensor/temperature", {
        "value": "22.5",
        "unit": "C",
        "location": "lab1",
    })

    # Read samples
    result = dds.read("sensor/temperature")
    for sample in result.samples:
        print(f"Temp: {sample.data['value']} {sample.data['unit']}")

    # Stream samples (bounded read -- monitor is infinite by default)
    for i, sample in enumerate(dds.monitor("sensor/temperature")):
        print(f"Live: {sample.data}")
        if i + 1 >= 10:
            break

    dds.disconnect()
```

## CLI

Inside `jmp shell`, the same operations are available as `j dds` commands:

```console
$ j dds
Usage: j dds [OPTIONS] COMMAND [ARGS]...

  DDS pub/sub communication

Options:
  --help  Show this message and exit.

Commands:
  connect       Connect to DDS domain
  create-topic  Create a topic: create-topic NAME FIELD1 FIELD2 ...
  disconnect    Disconnect from DDS domain
  info          Show DDS participant info
  monitor       Monitor samples from a topic
  publish       Publish JSON data to a topic: publish TOPIC '{"key": "val"}'
  read          Read samples from a topic
  topics        List registered topics
```

```console
$ j dds connect
$ j dds create-topic sensor/temperature value unit location
$ j dds publish sensor/temperature '{"value": "22.5", "unit": "C", "location": "lab1"}'
$ j dds read sensor/temperature
```

`create-topic` takes `--reliability`, `--durability` and `--history-depth`,
and `read` takes `--max-samples`. `monitor --count N` (default 10) polls the
topic N times, 0.1 s apart, and prints at most N samples.

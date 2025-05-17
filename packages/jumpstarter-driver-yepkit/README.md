# Yepkit driver

`jumpstarter-driver-yepkit` provides functionality for interacting with Yepkit
products.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-yepkit
```

## Configuration

Example configuration:

```yaml
export:
  power:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK25838"
      port: "1"

  power2:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK25838"
      port: "2"
```

### Config parameters

| Parameter | Description                                                       | Type | Required | Default |
| --------- | ----------------------------------------------------------------- | ---- | -------- | ------- |
| serial    | The serial number of the ykush hub, empty means auto-detection    | no   | None     |         |
| port      | The port number to be managed, "0", "1", "2", "a" which means all | str  | yes      | "a"     |

## API Reference

The yepkit ykush driver provides a `PowerClient` with the following API:

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :members: on, off
```

### Examples

Powering on and off a device
```{testcode}
:skipif: True
client.power.on()
time.sleep(1)
client.power.off()
```

### CLI access

```shell
$ sudo ~/.cargo/bin/uv run jmp shell --exporter-config ./packages/jumpstarter-driver-yepkit/examples/exporter.yaml
WARNING:Ykush:No serial number provided for ykush, using the first one found: YK25838
INFO:Ykush:Power OFF for Ykush YK25838 on port 1
INFO:Ykush:Power OFF for Ykush YK25838 on port 2

$$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  power   Generic power
  power2  Generic power

$$ j power on
INFO:Ykush:Power ON for Ykush YK25838 on port 1

$$ exit
```

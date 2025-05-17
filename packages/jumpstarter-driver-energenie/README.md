# EnerGenie

Drivers for EnerGenie products.

## EnerGenie driver

This driver provides a client for the [EnerGenie Programmable power switch](https://energenie.com/products.aspx?sg=239). The driver was tested on EG-PMS2-LAN device only but should be easy to support other devices.

**driver**: `jumpstarter_driver_energenie.driver.EnerGenie`

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-energenie
```

### Configuration

```yaml
export:
  power:
    type: jumpstarter_driver_energenie.driver.EnerGenie
    config:
      host: "192.168.0.1"
      password: "password"
      slot: "1"
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| host | The ip address of the EnerGenie system | string | yes | None |
| password | The password of the EnerGenie system | string | no | None |
| slot | The slot number to be managed, 1, 2, 3, 4 | int | yes | 1 |

### PowerClient API

The EnerGenie driver provides a `PowerClient` with the following API:

```{eval-rst}
.. autoclass:: jumpstarter_driver_power.client.PowerClient()
    :no-index:
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

### CLI

```bash
$ sudo uv run jmp exporter shell -c ./packages/jumpstarter-driver-energenie/examples/exporter.yaml

$$ j
Usage: j [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  power   Generic power

$$ j power on


$$ exit
```

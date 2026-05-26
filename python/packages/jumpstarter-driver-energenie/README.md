# Energenie PDU Driver

Drivers for EnerGenie products.

This driver provides a client for the EnerGenie Programmable power switch. The driver was tested on EG-PMS2-LAN device only but should be easy to support other devices.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-energenie
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-energenie/examples/config.yaml
:language: yaml
```

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| host | The IP address of the EnerGenie system | `str` | yes | |
| password | The password of the EnerGenie system | `str` | no | `"1"` |
| slot | The slot number to be managed (1, 2, 3, or 4) | `int` | no | `1` |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_energenie.driver.EnerGenie()
    :members:
```

### Examples

Powering on and off a device

```{literalinclude} ../../../../../packages/jumpstarter-driver-energenie/examples/usage.py
:language: python
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

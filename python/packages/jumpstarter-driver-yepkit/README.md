# Yepkit Driver

`jumpstarter-driver-yepkit` provides functionality for interacting with Yepkit
products.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-yepkit
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-yepkit/examples/config.yaml
:language: yaml
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
    :members: on, off, cycle
    :no-index:
```

### Examples

Powering on and off a device
```{literalinclude} ../../../../../packages/jumpstarter-driver-yepkit/examples/usage.py
:language: python
```

### CLI access

```{code-block} shell
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

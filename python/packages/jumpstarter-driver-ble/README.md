# BLE Driver

`jumpstarter-driver-ble` provides communication functionality via ble with the DUT.
The driver expects a ble service with a write and notify characteristic to send and receive data respectively.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ble
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-ble/examples/config.yaml
:language: yaml
```

### Config parameters

| Parameter        | Description                                        | Type | Required | Default |
| ---------------- | -------------------------------------------------- | ---- | -------- | ------- |
| address          | BLE address to connect to                      | str  | yes      |         |
| service_uuid     | BLE service uuid to connect to                     | str  | yes      |         |
| write_char_uuid  | BLE write characteristic to send data to DUT       | str  | yes      |         |
| notify_char_uuid | BLE notify characteristic to receive data from DUT | str  | yes      |         |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ble.client.BleWriteNotifyStreamClient()
    :members:
```

### CLI

The ble driver client comes with a CLI tool that can be used to interact with
the target device.

```console
jumpstarter ⚡ local ➤ j ble
Usage: j ble [OPTIONS] COMMAND [ARGS]...

  ble client

Options:
  --help  Show this message and exit.

Commands:
  info           Get target information
  start-console  Start BLE console
```

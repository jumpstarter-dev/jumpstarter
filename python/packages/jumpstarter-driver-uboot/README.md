# U-Boot Driver

`jumpstarter-driver-uboot` provides functionality for interacting with the
U-Boot bootloader. This driver does not interact with the DUT directly, instead
it should be configured with backing power and serial drivers.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-uboot
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-uboot/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_uboot.client.UbootConsoleClient()
    :members:
```

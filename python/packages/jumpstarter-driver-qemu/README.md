# QEMU Driver

`jumpstarter-driver-qemu` provides functionality for interacting with QEMU
virtualization platform.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-qemu
```

## Configuration

Example configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-qemu/examples/config.yaml
:language: yaml
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_qemu.driver.Qemu()
```

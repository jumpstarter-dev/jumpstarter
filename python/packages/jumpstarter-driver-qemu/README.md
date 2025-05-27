# QEMU driver

`jumpstarter-driver-qemu` provides functionality for interacting with QEMU
virtualization platform.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-qemu
```

## Configuration

Example configuration:

```yaml
export:
  qemu:
    type: jumpstarter_driver_qemu.driver.Qemu
    config:
      # Add required config parameters here
```

## API Reference

Add API documentation here.

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

```yaml
export:
  qemu:
    type: jumpstarter_driver_qemu.driver.Qemu
    config:
      # Add required config parameters here
```

### Serial ports

`serial_ports` gives the guest serial ports whose other end is a TCP socket beside QEMU: a byte stream between
something in the guest and something on the exporter.

```yaml
export:
  qemu:
    type: jumpstarter_driver_qemu.driver.Qemu
    config:
      serial_ports:
        bluetooth: { listen: 127.0.0.1:7301 }                        # /dev/hvc0
        trace: { device: pci-serial, connect: 127.0.0.1:7400 }       # /dev/ttyS0
```

| Parameter | Description | Default |
| --- | --- | --- |
| `device` | `virtconsole` (`/dev/hvcN` in a Linux guest, in the order given) or `pci-serial` (a 16550 UART, `/dev/ttySN`, for a guest without `virtio_console`; not with `virtio_transport: mmio`) | `virtconsole` |
| `listen` | `host:port` QEMU listens on. One client at a time; what the guest says while nobody is there is lost | |
| `connect` | `host:port` QEMU dials, and dials again a second after the connection went away. QEMU 9.2 or newer | |

A port has either `listen` or `connect`.

A guest's Bluetooth is one use: its HCI (H4) on a serial port, as on a board whose controller is on a UART.
In a Linux guest `btattach -B /dev/hvc0 -P h4` makes the port the kernel's `hci0` (the `hci_uart` module, which
distribution kernels have); a stack that speaks HCI itself opens the port. systemd starts a login prompt on a
virtio console by itself, which must not write into the stream: `systemctl mask serial-getty@hvc0`. On the exporter the socket is where a
controller is: the `link: remote-controller` of [`jumpstarter-driver-bluetooth`](bluetooth.md) joins it to the
controller simulator of another device of a lease, and any controller that speaks H4 over TCP can be put there.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_qemu.driver.Qemu()
```

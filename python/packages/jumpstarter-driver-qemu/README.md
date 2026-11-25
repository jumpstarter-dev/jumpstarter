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
      # every parameter is optional
      arch: x86_64
      mem: 2G
      disk_size: 20G
      hostfwd:
        ssh:
          hostport: 2222
          guestport: 22
```

### Configuration Parameters

| Parameter | Description | Type | Default |
| --- | --- | --- | --- |
| `arch` | Guest architecture; runs `qemu-system-<arch>` | str | exporter host architecture |
| `cpu` | CPU model passed to `-cpu` | str | `host` with KVM, otherwise `cortex-a57` (aarch64) or `qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt` (x86_64) |
| `smp` | Number of virtual CPUs | int | `2` |
| `mem` | Guest memory, e.g. `4G` | str | `512M` |
| `disk_size` | Grow the root disk to this size before boot (shrinking is refused) | str | none |
| `hostname`, `username`, `password` | Guest hostname and user created through cloud-init | str | `demo`, `jumpstarter`, `password` |
| `default_partitions` | Files used for `root`, `bios`, `OVMF_CODE.fd` or `OVMF_VARS.fd` when nothing was flashed to them | dict | `{}` |
| `virtio_transport` | `pci`, or `mmio` for firmware that cannot enumerate PCI, such as the U-Boot that boots aboot images | str | `pci` |
| `hostfwd` | Forwarded TCP ports, each exposed as a child of the same name; entries take `hostport`, `guestport` and optionally `hostaddr` (default `127.0.0.1`) | dict | `{}` |
| `fls_version` | Download this `fls` release from GitHub for `oci://` flashing instead of using `fls` from `PATH` | str | none |
| `fls_allow_custom_binaries` | Allow `fls_custom_binary_url` | bool | `false` |
| `fls_custom_binary_url` | URL of a custom `fls` binary | str | none |
| `flash_timeout` | Seconds before an `oci://` flash is stopped | int | `1800` |
| `launcher_socket` | jumpstarter-exec socket used to run QEMU in a separate container; set by the ExporterSet QEMU provisioners | str | none |

With `virtio_transport: mmio` the disk and network devices are attached over
virtio-mmio and no cloud-init disk is attached. Power on refuses to start with
less than `4G` of memory in this mode, because the aboot device tree hardcodes
a 4G layout. `examples/exporter-aboot-aarch64.yaml` boots an
automotive-image-builder aboot image this way.

The driver exposes `power`, `flasher`, `console` and `vnc` children, an `ssh`
child when `/dev/vhost-vsock` is usable on the exporter, and one child per
`hostfwd` entry. A `hostfwd` entry named `ssh`, like the one in the example
above, replaces the VSOCK `ssh` child.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_qemu.driver.Qemu()
```

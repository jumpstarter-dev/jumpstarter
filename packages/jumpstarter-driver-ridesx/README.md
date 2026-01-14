# RideSX driver

`jumpstarter-driver-ridesx` provides functionality for Qualcomm RideSX devices,
supporting fastboot flashing operations and power control through serial communication.

This is mainly tailored towards images that were produced using [automotive-image-builder](https://sigs.centos.org/automotive/getting-started/about-automotive-image-builder/):

```{code-block} console
automotive-image-builder build --target ridesx4 --export aboot.simg --mode package manifest.aib.yml ridesx.img
```

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ridesx
```

## Configuration

The RideSX driver supports two main components:

### Storage and Flashing Configuration

Example configuration for the RideSX driver:

```yaml
  storage:
    type: "jumpstarter_driver_ridesx.driver.RideSXDriver"
    config:
    children:
      # fastboot management serial port
      serial:
        type: "jumpstarter_driver_pyserial.driver.PySerial"
        config:
          url: "/dev/serial/by-id/usb-QUALCOMM_Inc._Embedded_Power_Measurement__EPM__device_98000205101B0224-if01"
          baudrate: 115200
  power:
    type: "jumpstarter_driver_ridesx.driver.RideSXPowerDriver"
    config:
    children:
      serial:
        type: "jumpstarter_driver_pyserial.driver.PySerial"
        config:
          url: "/dev/serial/by-id/usb-QUALCOMM_Inc._Embedded_Power_Measurement__EPM__device_98000205101B0224-if01"
          baudrate: 115200
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/serial/by-id/usb-FTDI_Qualcomm_AIR_8775_AI208U7YXA-if01-port01"
      baudrate: 115200

```

### CLI usage

```console
$ jmp shell -l board=qc-ridesx4
# Flash the device using the artifacts from automotive-image-builder, this uses 3 partition file systems
$$ j storage flash --target system_a:rootfs.simg --target system_b:qm_var.simg --target boot_a:aboot.img
$$ j power on
$$ j serial start-console
```

### Config parameters

#### RideSXDriver

| Parameter   | Description                                           | Type | Required | Default                     |
| ----------- | ----------------------------------------------------- | ---- | -------- | --------------------------- |
| storage_dir | Directory to store firmware images and temporary files | str  | no       | /var/lib/jumpstarter/ridesx |

#### RideSXPowerDriver

The power driver requires a `serial` child instance for communication.

### Required Children

Both drivers require:

| Child  | Description                                                  | Required |
| ------ | ------------------------------------------------------------ | -------- |
| serial | PySerial driver instance for communicating with the device  | yes      |

## API Reference

### RideSXClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_ridesx.client.RideSXClient()
    :members: flash, flash_images, boot_to_fastboot, cli
```

### RideSXPowerClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_ridesx.client.RideSXPowerClient()
    :members: on, off, cycle, rescue, serial
```

## Usage Examples

### Flash Single Partition

```{testcode}
# Flash a single partition
ridesx_client.flash("/path/to/boot.img", target="boot")
```

### Flash Multiple Partitions

```{testcode}
# Flash multiple partitions
partitions = {
    "boot": "/path/to/boot.img",
    "system": "/path/to/system.img",
    "userdata": "/path/to/userdata.img"
}
ridesx_client.flash(partitions)
```

### Flash with Compressed Images

The driver automatically handles compressed images (`.gz`, `.gzip`, `.xz`):

```{testcode}
# Flash compressed images - decompression is automatic
ridesx_client.flash("/path/to/boot.img.gz", target="boot")
```

### Power Control

```{testcode}
# Turn device power on
ridesx_power_client.on()

# Turn device power off
ridesx_power_client.off()

# Power cycle the device
ridesx_power_client.cycle(wait=5)  # Wait 5 seconds between off/on
```

## Features

- **Fastboot Support**: Automatically detects fastboot devices and flashes partitions
- **Compression Handling**: Supports automatic decompression of `.gz`, `.gzip`, and `.xz` files
- **Power Control**: Serial-based power control with on/off/cycle operations
- **Storage Management**: Built-in storage for firmware images with upload/download capabilities
- **Serial Communication**: Direct access to underlying serial interface for custom commands

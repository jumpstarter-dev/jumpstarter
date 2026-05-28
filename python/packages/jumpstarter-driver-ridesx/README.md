# RideSX Driver

`jumpstarter-driver-ridesx` provides functionality for Qualcomm RideSX devices,
supporting fastboot flashing operations and power control through serial communication.
It includes automatic compression handling (`.gz`, `.gzip`, `.xz`), built-in storage
for firmware images with upload/download capabilities, and direct access to the
underlying serial interface for custom commands.

This is mainly tailored towards images that were produced using [automotive-image-builder](https://sigs.centos.org/automotive/latest/getting-started/about-automotive-image-builder.html):

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

```{literalinclude} ../../../../../packages/jumpstarter-driver-ridesx/examples/config.yaml
:language: yaml
```

### CLI usage

```console
$ jmp shell -l board=qc-ridesx4
# Flash the device using the artifacts from automotive-image-builder, this uses 3 partition file systems
$$ j storage flash --target system_a:rootfs.simg --target system_b:qm_var.simg --target boot_a:aboot.img
$$ j power on
$$ j serial start-console
```

By default the device is powered off after flashing. Use ``--no-power-off`` to
leave it on.

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

## Usage

### Flash Single Partition

```{literalinclude} ../../../../../packages/jumpstarter-driver-ridesx/examples/usage.py
:language: python
```

### Flash Multiple Partitions

```{literalinclude} ../../../../../packages/jumpstarter-driver-ridesx/examples/usage_multi_flash.py
:language: python
```

### Flash with Compressed Images

The driver automatically handles compressed images (`.gz`, `.gzip`, `.xz`):

```{literalinclude} ../../../../../packages/jumpstarter-driver-ridesx/examples/usage_compressed_flash.py
:language: python
```

### Power Control

```{literalinclude} ../../../../../packages/jumpstarter-driver-ridesx/examples/usage_power.py
:language: python
```

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

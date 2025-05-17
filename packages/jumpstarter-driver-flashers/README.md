# Flashers

The flasher drivers are used to flash images to DUTs via network, typically
using TFTP and HTTP. It is designed to interact with the target bootloader and
busybox shell to flash the DUT.

All flasher drivers inherit from the
`jumpstarter_driver_flashers.driver.BaseFlasher` class, referencing their own
bundle of binary artifacts necessary to flash the DUT, like kernel/initram/dtbs.
See the [bundle](#oci-bundles) section for more details.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-flashers
```

## Available drivers and bundles

| Driver          | Bundle                                                       |
| --------------- | ------------------------------------------------------------ |
| TIJ784S4Flasher | quay.io/jumpstarter-dev/jumpstarter-flasher-ti-j784s4:latest |


## Driver configuration
**driver**: `jumpstarter_driver_flashers.driver.${DRIVER}`

```yaml
export:
  storage:
    type: "jumpstarter_driver_flashers.driver.TIJ784S4Flasher"
    children:
      serial:
        ref: "serial"
      power:
        ref: "power"
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_112214101760A-if00-port0"
      baudrate: 115200
  power:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK112233"
      port: "1"
```

flasher drivers require four children drivers:

| Child Driver | Description                                                                       | Auto-created |
| ------------ | --------------------------------------------------------------------------------- | ------------ |
| serial       | To communicate with the DUT via serial and drive the bootloader and busybox shell | No           |
| power        | To power on and off the DUT                                                       | No           |
| tftp         | To serve binaries via TFTP                                                        | Yes          |
| http         | To serve the images via HTTP                                                      | Yes          |

The power driver is used to control power cycling of the DUT, and the serial
interface is used to communicate with the DUT bootloader via serial. TFTP and
HTTP servers are used to serve images to the DUT bootloader and busybox shell.

### Config parameters

| Parameter      | Description                                | Type | Required | Default                      |
| -------------- | ------------------------------------------ | ---- | -------- | ---------------------------- |
| flasher_bundle | The OCI bundle to use for the flasher      | str  | yes      |                              |
| cache_dir      | The directory to cache the images          | str  | no       | /var/lib/jumpstarter/flasher |
| tftp_dir       | The directory to serve the images via TFTP | str  | no       | /var/lib/tftpboot            |
| http_dir       | The directory to serve the images via HTTP | str  | no       | /var/www/html                |


## BaseFlasher API

The `BaseFlasher` class provides a set of methods to flash the DUT,
```{eval-rst}
.. autoclass:: jumpstarter_driver_flashers.client.BaseFlasherClient()
    :members: flash, busybox_shell, bootloader_shell, use_dtb, use_initram, use_kernel
```

## CLI

The flasher driver provides a CLI to perform flashing, access to busybox shell
and uboot.

<!--
This doesn't work with sphinx-click, so we'll just use the raw CLI
```
.. click:: jumpstarter_driver_flashers.client:BaseFlasherClient.cli
    :prog: flasher
    :nested: full
```
-->
```shell
$ jmp shell -l board=ti-03
INFO:jumpstarter.client.lease:Created lease request for labels {'board': 'ti-03'} for 0:30:00
jumpstarter ⚡remote ➤ j storage
Usage: j storage [OPTIONS] COMMAND [ARGS]...

  Software-defined flasher interface

Options:
  --help  Show this message and exit.

Commands:
  bootloader-shell  Start a uboot/bootloader interactive console
  busybox-shell     Start a busybox shell
  flash             Flash image to DUT from file

```

### flash
```shell
Usage: j storage flash [OPTIONS] FILE

  Flash image to DUT from file

Options:
  --partition TEXT
  --os-image-checksum TEXT       SHA256 checksum of OS image (direct value)
  --os-image-checksum-file FILE  File containing SHA256 checksum of OS image
  --force-exporter-http          Force use of exporter HTTP
  --force-flash-bundle TEXT      Force use of a specific flasher OCI bundle
  --console-debug                Enable console debug mode
  --help                         Show this message and exit.
```

Example:
```
jumpstarter ⚡remote ➤ j storage flash https://autosd.sig.centos.org/AutoSD-9/nightly/TI/auto-osbuild-am69sk-autosd9-qa-regular-aarch64-1716106242.66b4d866.raw.xz
BaseFlasherClient - INFO - Writing image to storage in the background: /AutoSD-9/nightly/TI/auto-osbuild-am69sk-autosd9-qa-regular-aarch64-1716106242.66b4d866.raw.xz
BaseFlasherClient - INFO - Setting up flasher bundle files in exporter
BaseFlasherClient - INFO - Writing image from storage, with metadata: md5=None,size=592736176 etag="23546fb0-63045567a5b80"
SNMPServerClient - INFO - Starting power cycle sequence
SNMPServerClient - INFO - Waiting 2 seconds...
SNMPServerClient - INFO - Power cycle sequence complete
BaseFlasherClient - INFO - Waiting for U-Boot prompt...
BaseFlasherClient - INFO - Running DHCP to obtain network configuration...
BaseFlasherClient - INFO - Running command: dhcp
BaseFlasherClient - INFO - Running command: printenv netmask
BaseFlasherClient - INFO - discovered dhcp details: DhcpInfo(ip_address='x.x.x.x', gateway='x.x.x.x', netmask='255.255.255.0')
BaseFlasherClient - INFO - Image written to storage: /AutoSD-9/nightly/TI/auto-osbuild-am69sk-autosd9-qa-regular-aarch64-1716106242.66b4d866.raw.xz
BaseFlasherClient - INFO - Running command: setenv serverip 'x.x.x.x'
BaseFlasherClient - INFO - Running command: tftpboot 0x82000000 J784S4XEVM.flasher.img
BaseFlasherClient - INFO - Running command: tftpboot 0x84000000 k3-j784s4-evm.dtb
BaseFlasherClient - INFO - Running boot command: booti 0x82000000 - 0x84000000
BaseFlasherClient - INFO - Using target block device: /dev/mmcblk1
BaseFlasherClient - INFO - Running preflash command: dd if=/dev/zero of=/dev/mmcblk0 bs=512 count=34
BaseFlasherClient - INFO - Running preflash command: dd if=/dev/zero of=/dev/mmcblk1 bs=512 count=34
BaseFlasherClient - INFO - Waiting until the http image preparation in storage is completed
BaseFlasherClient - INFO - Flash progress: 25.00 MB, Speed: 15.78 MB/s
...
...
BaseFlasherClient - INFO - Flash progress: 5086.12 MB, Speed: 13.77 MB/s
BaseFlasherClient - INFO - Flash progress: 5102.94 MB, Speed: 12.93 MB/s
BaseFlasherClient - INFO - Flushing buffers
BaseFlasherClient - INFO - Flashing completed in 7:26
BaseFlasherClient - INFO - Powering off target
```

### bootloader-shell
```shell
Usage: j storage bootloader-shell [OPTIONS]

  Start a uboot/bootloader interactive console

Options:
  --console-debug  Enable console debug mode
  --help           Show this message and exit.
```

Example
```
jumpstarter ⚡remote ➤ j storage bootloader-shell
BaseFlasherClient - INFO - Setting up flasher bundle files in exporter
SNMPServerClient - INFO - Starting power cycle sequence
SNMPServerClient - INFO - Waiting 2 seconds...
SNMPServerClient - INFO - Power cycle sequence complete
BaseFlasherClient - INFO - Waiting for U-Boot prompt...
=> version
U-Boot 2024.01-rc3 (Jan 09 2024 - 00:00:00 +0000)

gcc (GCC) 11.4.1 20231218 (Red Hat 11.4.1-3)
GNU ld version 2.35.2-42.el9
```
### busybox-shell
```shell
Usage: j storage busybox-shell [OPTIONS]

  Start a busybox interactive console

Options:
  --console-debug  Enable console debug mode
  --help           Show this message and exit.
```

Example
```
jumpstarter ⚡remote ➤ j storage busybox-shell
BaseFlasherClient - INFO - Setting up flasher bundle files in exporter
SNMPServerClient - INFO - Starting power cycle sequence
SNMPServerClient - INFO - Waiting 2 seconds...
SNMPServerClient - INFO - Power cycle sequence complete
BaseFlasherClient - INFO - Waiting for U-Boot prompt...
BaseFlasherClient - INFO - Running DHCP to obtain network configuration...
BaseFlasherClient - INFO - Running command: dhcp
BaseFlasherClient - INFO - Running command: printenv netmask
BaseFlasherClient - INFO - discovered dhcp details: DhcpInfo(ip_address='10.26.28.138', gateway='10.26.28.254', netmask='255.255.255.0')
BaseFlasherClient - INFO - Running command: setenv serverip '10.26.28.62'
BaseFlasherClient - INFO - Running command: tftpboot 0x82000000 J784S4XEVM.flasher.img
BaseFlasherClient - INFO - Running command: tftpboot 0x84000000 k3-j784s4-evm.dtb
BaseFlasherClient - INFO - Running boot command: booti 0x82000000 - 0x84000000
# uname -a
Linux buildroot 6.1.46-dirty #2 SMP PREEMPT Thu Mar 14 14:37:01 UTC 2024 aarch64 GNU/Linux
#
```

## Examples

Flash the device with a specific image
```python
flasherclient.flash("/path/to/image.raw.xz")
```

Flash the device with a specific image from a remote URL
```python
flasherclient.flash("https://autosd.sig.centos.org/AutoSD-9/nightly/TI/auto-osbuild-j784s4evm-autosd9-qa-regular-aarch64-1716106242.66b4d866.raw.xz")
```

Flash into a specific partition
```python
flasherclient.flash("/path/to/image.raw.xz", partition="emmc")
```


## Examples of utility consoles

In addition to the flashing mechanisms, the flasher drivers also provide a way
to access the DUT bootloader and busybox shell for convenience and debugging,
when using the `busybox_shell` and `bootloader_shell` methods the embedded http
and tftp servers will be online and serving the images from the flasher bundle.

Get the busybox shell on the device
```python
with flasherclient.busybox_shell() as serial:
    serial.send("ls -la\n")
    serial.expect("#")
    print(serial.before)
```

Get the bootloader shell on the device
```python
with flasherclient.bootloader_shell() as serial:
    serial.send("version\n")
    serial.expect("=>")
    print(serial.before)
```

## oci-bundles

The flasher drivers require some artifacts and basic information about the
target device to operate. To make this easy to distribute and use, we use OCI
bundles to package the artifacts and metadata.

The bundle is a container that uses [oras](https://oras.land/) to transport the
artifacts and metadata. It is a container that contains the following:
- `manifest.yaml`: The manifest file that describes the bundle
- `data/*`: The artifacts, including kernel, initram, dtbs, etc.

## The format of the manifest is as follows:

```{literalinclude} ../../../../../packages/jumpstarter-driver-flashers/oci_bundles/test/manifest.yaml
:language: yaml
```

## Table with the spec fields of the manifest:

| Field                | Description                                                                | Default |
| -------------------- | -------------------------------------------------------------------------- | ------- |
| `manufacturer`       | Name of the device manufacturer                                            |         |
| `link`               | URL to device documentation or manufacturer website                        |         |
| `bootcmd`            | Command used to boot the device (e.g. booti, bootz)                        |         |
| `default_target`     | Default target device to flash to if none specified                        |         |
| `targets`            | Map of target names to device paths                                        |         |
| `login.type`         | Type of login shell                                                        | busybox |
| `login.login_prompt` | Expected login prompt string                                               | login:  |
| `login.username`     | Username to log in with, leave empty if not needed                         |         |
| `login.password`     | Password for login, leave empty if not needed                              |         |
| `login.prompt`       | Shell prompt after successful login                                        | #       |
| `preflash_commands`  | List of commands to run before flashing, useful to clear boot entries, etc |         |
| `kernel.file`        | Path to kernel image within bundle                                         |         |
| `kernel.address`     | Memory address to load kernel to                                           |         |
| `initram.file`       | Path to initramfs within bundle (if any)                                   |         |
| `initram.address`    | Memory address to load initramfs to (if any)                               |         |
| `dtb.default`        | Default DTB variant to use                                                 |         |
| `dtb.address`        | Memory address to load DTB to                                              |         |
| `dtb.variants`       | Map of DTB variant names to files                                          |         |

## Examples

An example bundle for the TI J784S4XEVM looks like this:

```{literalinclude} ../../../../../packages/jumpstarter-driver-flashers/oci_bundles/ti_j784s4xevm/manifest.yaml
:language: yaml
```

You can find a script to build and push a bundle to a registry here:
[oci_bundles](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-flashers/oci_bundles)

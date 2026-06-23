# SD Wire Driver

`jumpstarter-driver-sdwire` provides functionality for using the SDWire storage
multiplexer. This device multiplexes an SD card between the DUT and the exporter
host.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-sdwire
```

## Configuration

Example configuration:

```{literalinclude} sdwire.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/sdwire.yaml").instantiate() # doctest: +ELLIPSIS
Traceback (most recent call last):
...
FileNotFoundError: failed to find sd-wire device...
```

## Unprogrammed (factory-default) SD Wire devices

SD Wires are normally initialized with `sd-mux-ctrl --init`, which rewrites the
FTDI FT200X EEPROM to the Samsung VID/PID (`0x04E8`/`0x6001`, product `sd-wire`)
**and** configures the FT200X's `CBUS0` pin as a GPIO so the mux can be switched.

A device that still has the **factory-default FTDI EEPROM** (`0x0403`/`0x6015`)
is also supported, but with two requirements:

1. **A `serial` must be configured.** A bare FT200X has no reliable runtime
   signature that distinguishes an SD Wire from any other FT200X, so the driver
   will only bind one when you pin it by serial number.

2. **`CBUS0` must be set to `GPIO` in the EEPROM (one-time fix).** This is the
   critical prerequisite:

   > The FT200X's CBUS bitbang mode (used to switch the mux) requires `CBUS0` to
   > be configured as `GPIO`/`IOMODE` in the EEPROM. On an unprogrammed FT200X
   > `CBUS0` defaults to a fixed function (e.g. `TXLED`) and **completely ignores
   > bitbang commands** — the green LED never lights and the mux never switches
   > to DUT.

   The driver does **not** reprogram the EEPROM; it only sends the runtime
   bitbang command and assumes `CBUS0` is already a GPIO. Perform this one-time
   fix first (it preserves the original VID/PID and only changes `CBUS0`):

   ```python
   from pyftdi.eeprom import FtdiEeprom

   e = FtdiEeprom()
   e.open("ftdi://0x0403:0x6015/1")
   e.set_property("cbus_func_0", "GPIO")   # FT200X uses 'GPIO', not 'IOMODE'
   e.commit(dry_run=False)
   ```

   After flashing, **unplug and replug** the SD Wire USB cable. If you run the
   driver against an unprogrammed device that has *not* had this fix applied,
   the device is found but `host()`/`dut()` silently fail to move the mux.

## macOS notes

On macOS (`Darwin`) the driver does extra work that is unnecessary on Linux:

- **`dut()` ejects the card before switching.** The mux only switches when the
  SD bus is fully idle; while macOS holds the SMSC reader open it keeps polling
  the card. The driver runs `diskutil eject` (SCSI `STOP UNIT`) first. If the
  disk cannot be determined, `dut()` aborts rather than risk corrupting a mounted
  volume.
- **Power on the DUT immediately after `dut()` (< ~500 ms).** The mux has a
  protection circuit that reverts to HOST if it sees no SD activity on the DUT
  side shortly after switching.
- **`host()` power-cycles the reader's hub port.** Because the prior `eject`
  leaves the SMSC reader stopped, `host()` routes the card back and *then*
  power-cycles port 1 of *this* SD Wire's internal hub to force a clean
  re-enumeration (correlated by USB topology so other attached SD Wires are not
  disturbed).
- **Storage discovery uses `system_profiler`** (pyudev is Linux-only) and may
  take a moment to re-enumerate after a switch, so reads/writes retry discovery
  up to `storage_timeout`.

## API Reference

The SDWire driver implements the `StorageMuxClient` class, which is a generic
storage class.

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.StorageMuxClient()
    :members:
```

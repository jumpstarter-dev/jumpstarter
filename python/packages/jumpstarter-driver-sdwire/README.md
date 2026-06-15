# SD Wire Driver

`jumpstarter-driver-sdwire` provides functionality for using SD Wire storage
multiplexers. These devices route an SD card between the DUT and the exporter
host so you can flash or read the card from the host, then switch it to the DUT.

The same `SDWire` driver class supports all variants below. **No model selector is
required** — the driver picks the correct protocol from the USB identity of the
attached device.

## Supported devices

| Variant | Typical hardware | USB identity | Mux control | Init / setup |
|---------|------------------|--------------|-------------|--------------|
| **Original SD Wire** (3mdeb / Tizen) | FT200X + internal SMSC reader | `0x04E8:0x6001`, product `sd-wire` | FTDI CBUS bitbang | Run `sd-mux-ctrl --init` once |
| **SDWireC** (Badgerd) | Same FT200X design as above | `0x04E8:0x6001`, product `sd-wire` | FTDI CBUS bitbang | Run `sd-mux-ctrl --init` once |
| **Unprogrammed FT200X** | Factory-default EEPROM | `0x0403:0x6015` | FTDI CBUS bitbang | Set `serial` in config; one-time CBUS0 GPIO fix (see below) |
| **SDWire3** (Badgerd Gen2) | Realtek USB3 reader + mux | `0x0BDA:0x0316`, product `USB3.0-CRW` | Kernel driver attach/detach + USB reset | Plug and play |

### How switching differs

**FT200X-based devices** (original SD Wire, SDWireC, unprogrammed FT200X):

- The FT200X is a separate USB function from the SMSC SD card reader behind an
  internal hub.
- The driver sends FTDI vendor requests (`select(0xF1)` = host, `select(0xF0)` =
  DUT).
- On macOS, `host()` also power-cycles the reader's hub port so the card
  re-enumerates after an eject.

**SDWire3**:

- The card reader and mux control share **one** USB mass-storage interface.
- **Host:** attach kernel mass-storage driver + USB reset → card visible to the
  host (`/dev/disk…` / `/dev/disk/by-diskseq/…`).
- **DUT:** detach kernel driver + USB reset → card routed to the DUT slot.
- No SMSC hub, no FTDI EEPROM programming, no `sd-mux-ctrl`.

The exported API is the same for all variants: `host()`, `dut()`, `off()`,
`read()`, and `write()`.

### Backwards compatibility

Existing exporter configs and FT200X-based hardware **keep working unchanged**:

- Detection order prefers SDWire3 when `0x0BDA:0x0316` is present, then
  programmed `sd-wire` devices, then unprogrammed FT200X (only when `serial` is
  set).
- All previous config fields (`serial`, `storage_device`, timeouts) apply to
  every variant where relevant.
- FT200X `query()` still uses the FTDI status request; SDWire3 `query()` uses
  whether the kernel mass-storage driver is attached.

If you have **both** an SDWire3 and an FT200X SD Wire on the same machine, set
`serial` so the driver binds the intended unit.

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

### Config fields

| Field | Required | Applies to | Purpose |
|-------|----------|------------|---------|
| `serial` | Optional (required for unprogrammed FT200X; recommended with multiple devices) | All | Pin a specific unit when several are attached |
| `storage_device` | Optional | All | Block device path; skips auto-discovery when set |
| `storage_timeout` | Optional | All | Seconds to wait for the card to appear after switching to host |
| `storage_leeway` | Optional | All | Write safety margin (see `StorageMuxClient`) |
| `storage_fsync_timeout` | Optional | All | `fsync` timeout for writes |

There is **no** `device_type` or `model` setting. Auto-detection is intentional
so one exporter config works across lab hardware generations.

### `serial` values by variant

- **Programmed FT200X** (`sd-wire`): the string from `sd-mux-ctrl --init` /
  `usb.util.get_string(iSerialNumber)`, e.g. `sd-wire_11`.
- **Unprogrammed FT200X**: the factory FTDI serial from `lsusb` / `dmesg`; the
  driver will not bind an unprogrammed FT200X unless this is set.
- **SDWire3**: any of:
  - USB serial string (e.g. `20120501030900000`)
  - Composite identity `<serial>.<usb.port.path>` (e.g. `20120501030900000.1`)
  - Bus and address `<bus>.<address>` (e.g. `2.2`)

### `storage_device` auto-discovery

When unset, the driver resolves the block device automatically:

| Variant | Linux | macOS |
|---------|-------|-------|
| FT200X | pyudev: SMSC reader sibling of the FT200X | `system_profiler`: reader next to FT200X in USB tree |
| SDWire3 | pyudev: block device under the same USB device | `ioreg`: whole disk under `USB3.0-CRW@…` (fallback when `system_profiler` is empty) |

Set `storage_device` explicitly if auto-discovery is ambiguous or you want a
stable path such as `/dev/disk/by-diskseq/N`.

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/sdwire.yaml").instantiate() # doctest: +ELLIPSIS
Traceback (most recent call last):
...
FileNotFoundError: failed to find sd-wire device...
```

## Unprogrammed (factory-default) FT200X devices

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

On macOS (`Darwin`) the driver does extra work that is unnecessary on Linux.

**All variants — `dut()`:**

- Ejects the card with `diskutil eject` before switching so the SD bus is idle.
  If the disk cannot be determined, `dut()` aborts rather than risk corrupting a
  mounted volume.

**FT200X-based devices only:**

- **Power on the DUT within ~500 ms after `dut()`.** The mux protection circuit
  reverts to HOST if it sees no SD activity on the DUT side shortly after
  switching.
- **`host()` power-cycles the reader's hub port** after routing the card back,
  so the SMSC reader re-enumerates cleanly after the prior eject.

**SDWire3 only:**

- Switching uses libusb to detach/attach the kernel mass-storage driver. **Run
  the exporter with root on macOS:**

  ```console
  sudo uv run jmp shell --exporter-config packages/jumpstarter-driver-sdwire/examples/exporter.yaml
  ```

- Storage discovery uses `ioreg` (recent macOS versions often return an empty
  USB tree from `system_profiler`).

**All variants — reads/writes:**

- Storage may take a moment to re-enumerate after a switch; `read()` / `write()`
  retry discovery up to `storage_timeout`.

**SDWire3 at startup:**

- If the mux is already on the DUT side, no host disk is visible during init.
  The driver logs a warning and continues; `host()` / `read()` / `write()` wait
  until the card appears.

## API Reference

The SDWire driver implements the `StorageMuxClient` class, which is a generic
storage class.

```{eval-rst}
.. autoclass:: jumpstarter_driver_opendal.client.StorageMuxClient()
    :members:
```

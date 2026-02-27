# NoyitoPowerSerial / NoyitoPowerHID Driver

`jumpstarter-driver-noyito-relay` provides Jumpstarter power drivers for NOYITO
USB relay boards in 1, 2, 4, and 8-channel variants.

Two hardware series are supported:

- **`NoyitoPowerSerial`** — 1/2-channel boards using a CH340 USB-to-serial chip
  (serial port, supports status query)
- **`NoyitoPowerHID`** — 4/8-channel "HID Drive-free" boards presenting as a
  USB HID device (no serial port, status query not available)

Both use the same 4-byte binary command protocol (`A0` + channel + state +
checksum).

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-noyito-relay
```

If you are using `NoyitoPowerHID`, the `hid` Python package requires the native
`hidapi` shared library. Install it for your OS before use:

| OS | Command |
|----|---------|
| macOS | `brew install hidapi` |
| Debian/Ubuntu | `sudo apt-get install libhidapi-hidraw0` |
| Fedora/RHEL | `sudo dnf install hidapi` |

## Board Detection

To determine which driver to use, check whether the board appears as a serial
port or a HID device:

- **Serial port** (`/dev/ttyUSB*`, `/dev/tty.usbserial-*`): Use `NoyitoPowerSerial`
  (1/2-channel CH340 board)
- **No serial port / HID only**: Use `NoyitoPowerHID` (4/8-channel HID
  Drive-free board). Confirm with `lsusb` — the NOYITO HID module appears with
  VID `0x1409` / PID `0x07D7` (decimal: 5131 / 2007).

## `NoyitoPowerSerial` (1/2-Channel Serial)

### Hardware Notes

- **Purchase**: [NOYITO 2-Channel USB Relay Module (Amazon)](https://www.amazon.com/NOYITO-2-Channel-Module-Control-Intelligent/dp/B081RM7PMY/)
- **Chip**: CH340 USB-to-serial
- **Baud rate**: 9600
- **Default port**: `/dev/ttyUSB0` (Linux) — may appear as `/dev/tty.usbserial-*` on macOS
- **Channels**: 1 or 2 independent relay channels on one USB port
- **Supply voltage**: 5 V via USB

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `port` | `str` | *(required)* | Serial port path, e.g. `/dev/ttyUSB0` |
| `channel` | `int` | `1` | Relay channel to control (`1` or `2`) |
| `dual` | `bool` | `false` | Switch both channels simultaneously |

Example configuration controlling both channels independently:

```yaml
export:
  relay1:
    type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerSerial
    config:
      port: "/dev/ttyUSB0"
      channel: 1
  relay2:
    type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerSerial
    config:
      port: "/dev/ttyUSB0"
      channel: 2
```

### API Reference

Implements `PowerInterface` (provides `on`, `off`, `read`, and `cycle` via
`PowerClient`).

| Method | Description |
|--------|-------------|
| `on()` | Energise the configured relay channel |
| `off()` | De-energise the configured relay channel |
| `read()` | Yields a single `PowerReading(voltage=0.0, current=0.0)` |
| `status()` | Returns the channel state string, e.g. `"on"`, `"off"`, or `"partial"` |

### CLI Usage

Inside a `jmp exporter shell`:

```shell
# Power on relay 1
j relay1 on

# Query state of relay 1
j relay1 status
# on

# Power cycle relay 2 with a 3-second wait
j relay2 cycle --wait 3

# Power off relay 1
j relay1 off
```

## `NoyitoPowerHID` (4/8-Channel HID Drive-free)

### Hardware Notes

- **Purchase (4-channel)**: [NOYITO 4-Channel HID Drive-free USB Relay (Amazon)](https://www.amazon.com/NOYITO-Drive-Free-Computer-2-Channel-Micro-USB/dp/B0B538N95Q)
- **Purchase (8-channel)**: [NOYITO 8-Channel HID Drive-free USB Relay (Amazon)](https://www.amazon.com/NOYITO-Drive-Free-Computer-2-Channel-Micro-USB/dp/B0B536M5MH)
- **Interface**: USB HID (no serial port)
- **Default VID/PID**: `5131` / `2007` (0x1409 / 0x07D7)
- **Channels**: 4 or 8 independent relay channels
- **Supply voltage**: 5 V via USB
- **Status query**: Not supported by this hardware series

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_channels` | `int` | `4` | Number of relay channels on the board (`4` or `8`) |
| `channel` | `int` | `1` | Relay channel to control (`1`..`num_channels`) |
| `all_channels` | `bool` | `false` | Fire every channel simultaneously |
| `vendor_id` | `int` | `5131` | USB vendor ID (override if needed) |
| `product_id` | `int` | `2007` | USB product ID (override if needed) |

Example configuration for a 4-channel board (channel 1) and an 8-channel board
(all channels simultaneously):

```yaml
export:
  relay_4ch_ch1:
    type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerHID
    config:
      num_channels: 4
      channel: 1
  relay_8ch_all:
    type: jumpstarter_driver_noyito_relay.driver.NoyitoPowerHID
    config:
      num_channels: 8
      channel: 1
      all_channels: true
```

### API Reference

Implements `PowerInterface` (provides `on`, `off`, `read`, and `cycle` via
`PowerClient`).

| Method | Description |
|--------|-------------|
| `on()` | Energise the configured relay channel(s) |
| `off()` | De-energise the configured relay channel(s) |
| `read()` | Yields a single `PowerReading(voltage=0.0, current=0.0)` |

> **Note**: `status()` is not available for HID boards. The hardware does not
> support a status query command.

### CLI Usage

Inside a `jmp exporter shell`:

```shell
# Power on relay channel 1 of the 4-ch board
j relay_4ch_ch1 on

# Power cycle with a 1-second wait
j relay_4ch_ch1 cycle --wait 1

# Power off
j relay_4ch_ch1 off

# Power on all 8 channels simultaneously
j relay_8ch_all on
```
